//! ITU-R BS.2127 Cartesian object-size panning.

const NUM_VIRTUAL_SOURCES: usize = 40;
const NEG130_DB: f64 = 3.162_277_660_168_379e-7;

pub fn gains(channel_positions: &[[f64; 3]], position: [f64; 3], size: f64) -> Vec<f64> {
    if channel_positions.is_empty() {
        return Vec::new();
    }
    let nx = NUM_VIRTUAL_SOURCES;
    let ny = NUM_VIRTUAL_SOURCES;
    let has_three_planes = unique(channel_positions, 2).len() >= 3;
    let nz = if has_three_planes {
        NUM_VIRTUAL_SOURCES
    } else {
        NUM_VIRTUAL_SOURCES / 2
    };
    let xs = grid(-1.0, 1.0, nx);
    let ys = grid(-1.0, 1.0, ny);
    let zs = if has_three_planes {
        grid(-1.0, 1.0, nz)
    } else {
        grid(0.0, 1.0, nz)
    };
    let [xo, yo, mut zo] = position;
    if !has_three_planes {
        zo = zo.max(0.0);
    }
    let scaled = scale_size(size.clamp(0.0, 1.0));
    let sx = scaled.max(2.0 / (nx - 1) as f64);
    let sy = scaled.max(2.0 / (ny - 1) as f64);
    let sz = scaled.max(2.0 / (nz - 1) as f64);
    let dim = (0..3)
        .filter(|&axis| unique(channel_positions, axis).len() > 1)
        .count();
    let s_eff = match dim {
        0 | 1 => sx,
        2 => 0.75 * sx.max(sy) + 0.25 * sx.min(sy),
        _ => {
            let mut sizes = [sx, sy, sz];
            sizes.sort_by(f64::total_cmp);
            6.0 / 9.0 * sizes[2] + 2.0 / 9.0 * sizes[1] + sizes[0] / 9.0
        }
    };
    let p = if s_eff <= 0.5 {
        6.0
    } else {
        6.0 - 4.0 * (s_eff - 0.5) / 2.3
    };
    let boundary = [xo + 1.0, 1.0 - xo, yo + 1.0, 1.0 - yo, zo + 1.0, 1.0 - zo]
        .into_iter()
        .take(dim * 2)
        .fold(f64::INFINITY, f64::min);
    let h = |s: f64| {
        if boundary >= 2.0 * s && boundary >= 0.4 {
            let n = (2.0 * s).max(0.4);
            (n.powi(3) / (0.16 * 2.0 * s)).cbrt()
        } else {
            (boundary / 2.0 * (boundary / 0.4).powi(2)).cbrt()
        }
    };
    let mu = match dim {
        0 | 1 => h(sx).powi(3),
        2 => (h(sx) * h(sy)).powf(1.5),
        _ => h(sx) * h(sy) * h(sz),
    };
    let weights = |origin: f64, size: f64, values: &[f64], z: bool| {
        values
            .iter()
            .map(|&value| {
                let denominator = if z { size } else { 2.0 * size };
                let exponent = -((1.5 * (value - origin) / denominator).powi(4)).min(6.5);
                10.0_f64.powf(exponent)
                    * if z {
                        (value * std::f64::consts::PI * 3.0 / 7.0).cos()
                    } else {
                        1.0
                    }
            })
            .collect::<Vec<_>>()
    };
    let wx = weights(xo, sx, &xs, false);
    let wy = weights(yo, sy, &ys, false);
    let wz = weights(zo, sz, &zs, true);
    let (gx, gy, gz) = point_gains(channel_positions, &xs, &ys, &zs);
    let calc_f = |g: &[Vec<f64>], w: &[f64]| {
        g.iter()
            .map(|row| {
                let value = row
                    .iter()
                    .zip(w)
                    .map(|(gain, weight)| (gain * weight).powf(p))
                    .sum::<f64>();
                if value < NEG130_DB {
                    0.0
                } else {
                    value
                }
            })
            .collect::<Vec<_>>()
    };
    let fx = calc_f(&gx, &wx);
    let fy = calc_f(&gy, &wy);
    let fz = calc_f(&gz, &wz);
    let inside = normalize(
        &fx.iter()
            .zip(&fy)
            .zip(&fz)
            .map(|((&x, &y), &z)| x * y * z)
            .collect::<Vec<_>>(),
    );
    let bound = (0..channel_positions.len())
        .map(|i| {
            (gx[i][0] * wx[0]).powf(p) * fy[i] * fz[i]
                + (gx[i][nx - 1] * wx[nx - 1]).powf(p) * fy[i] * fz[i]
                + fx[i] * (gy[i][0] * wy[0]).powf(p) * fz[i]
                + fx[i] * (gy[i][ny - 1] * wy[ny - 1]).powf(p) * fz[i]
                + fx[i] * fy[i] * (gz[i][0] * wz[0]).powf(p)
                + fx[i] * fy[i] * (gz[i][nz - 1] * wz[nz - 1]).powf(p)
                + mu * inside[i]
        })
        .map(|value| value.powf(1.0 / p))
        .collect::<Vec<_>>();
    let sized = normalize(&bound);
    let point = point(channel_positions, [xo, yo, zo]);
    let (alpha, beta) = if s_eff < 0.2 {
        (
            (s_eff * std::f64::consts::PI / 0.4).cos(),
            (s_eff * std::f64::consts::PI / 0.4).sin(),
        )
    } else {
        (0.0, 1.0)
    };
    normalize(
        &point
            .iter()
            .zip(sized)
            .map(|(&a, b)| alpha * a + beta * b)
            .collect::<Vec<_>>(),
    )
}

/// Apply Dolby-profile channel lock and preset zone exclusions around BS.2127 panning.
pub fn gains_with_metadata(
    channel_positions: &[[f64; 3]],
    priorities: &[[i64; 4]],
    mut position: [f64; 3],
    size: f64,
    channel_lock: bool,
    zone_exclusion: &[&str],
) -> Vec<f64> {
    let mut excluded = vec![false; channel_positions.len()];
    for (index, point) in channel_positions.iter().enumerate() {
        excluded[index] = zone_exclusion
            .iter()
            .filter_map(|zone| zone_bounds(zone))
            .any(|bounds| {
                point[0] >= bounds[0]
                    && point[0] <= bounds[1]
                    && point[1] >= bounds[2]
                    && point[1] <= bounds[3]
                    && point[2] >= bounds[4]
                    && point[2] <= bounds[5]
            });
    }
    for index in 0..channel_positions.len() {
        let point = channel_positions[index];
        if excluded[index] && point[0].abs() == 1.0 && point[1].abs() != 1.0 {
            for (other, candidate) in channel_positions.iter().enumerate() {
                if candidate[1] == point[1] && candidate[2] == point[2] {
                    excluded[other] = true;
                }
            }
        }
    }
    if excluded.iter().all(|value| *value) {
        excluded.fill(false);
    }
    if channel_lock {
        let weighted_distance = |point: [f64; 3]| {
            (point[0] - position[0]).powi(2) / 16.0
                + 4.0 * (point[1] - position[1]).powi(2)
                + 32.0 * (point[2] - position[2]).powi(2)
        };
        position = channel_positions
            .iter()
            .enumerate()
            .filter(|(index, _)| !excluded[*index])
            .min_by(|(a, left), (b, right)| {
                weighted_distance(**left)
                    .total_cmp(&weighted_distance(**right))
                    .then_with(|| priorities[*a].cmp(&priorities[*b]))
            })
            .map(|(_, point)| *point)
            .unwrap_or(position);
    }
    let active: Vec<[f64; 3]> = channel_positions
        .iter()
        .enumerate()
        .filter_map(|(index, point)| (!excluded[index]).then_some(*point))
        .collect();
    let mut active_gains = gains(&active, position, size.clamp(0.0, 1.0));
    if size == 0.0 {
        for gain in &mut active_gains {
            if *gain < 1e-6 {
                *gain = 0.0;
            }
        }
        let norm = active_gains
            .iter()
            .map(|gain| gain * gain)
            .sum::<f64>()
            .sqrt();
        if norm > 0.0 {
            for gain in &mut active_gains {
                *gain /= norm;
            }
        }
    }
    let mut next = 0;
    excluded
        .into_iter()
        .map(|is_excluded| {
            if is_excluded {
                0.0
            } else {
                let gain = active_gains[next];
                next += 1;
                gain
            }
        })
        .collect()
}

fn zone_bounds(name: &str) -> Option<[f64; 6]> {
    Some(match name {
        "ZM1" => [-1.0, 1.0, -1.0, -0.41934, -0.499, 0.499],
        "ZM2L" => [-1.0, -0.75806, -0.41934, 0.83871, -0.499, 0.499],
        "ZM2R" => [0.75806, 1.0, -0.41934, 0.83871, -0.499, 0.499],
        "ZM3L" => [-1.0, -0.16129, 0.5, 1.0, -0.499, 0.499],
        "ZM3Lss" => [-1.0, -0.51611, -0.707, 0.49999, -0.499, 0.499],
        "ZM3R" => [0.16129, 1.0, 0.5, 1.0, -0.499, 0.499],
        "ZM3Rss" => [0.51611, 1.0, -0.707, 0.49999, -0.499, 0.499],
        "ZM4" => [-1.0, 1.0, -1.0, 0.83871, -0.499, 0.499],
        "ZM5" => [-1.0, 1.0, 0.5, 1.0, -0.499, 0.499],
        "ZB" => [-1.0, 1.0, -1.0, 1.0, -1.0, -0.4995],
        "ZT" => [-1.0, 1.0, -1.0, 1.0, 0.4995, 1.0],
        _ => return None,
    })
}

fn scale_size(value: f64) -> f64 {
    let points = [(0.0, 0.0), (0.2, 0.3), (0.5, 1.0), (0.75, 1.8), (1.0, 2.8)];
    for pair in points.windows(2) {
        if value <= pair[1].0 {
            return pair[0].1
                + (value - pair[0].0) / (pair[1].0 - pair[0].0) * (pair[1].1 - pair[0].1);
        }
    }
    2.8
}
fn grid(start: f64, end: f64, count: usize) -> Vec<f64> {
    (0..count)
        .map(|i| start + (end - start) * i as f64 / (count - 1) as f64)
        .collect()
}
fn unique(points: &[[f64; 3]], axis: usize) -> Vec<f64> {
    let mut values: Vec<f64> = points.iter().map(|p| p[axis]).collect();
    values.sort_by(f64::total_cmp);
    values.dedup_by(|a, b| (*a - *b).abs() < 1e-12);
    values
}
fn bounds(values: impl Iterator<Item = f64>, value: f64) -> (Option<f64>, Option<f64>) {
    let mut lo: Option<f64> = None;
    let mut hi: Option<f64> = None;
    for candidate in values {
        if candidate <= value {
            lo = Some(lo.map_or(candidate, |bound| bound.max(candidate)));
        }
        if candidate >= value {
            hi = Some(hi.map_or(candidate, |bound| bound.min(candidate)));
        }
    }
    (lo, hi)
}

fn balance_gain(point: f64, value: f64, bounds: (Option<f64>, Option<f64>)) -> f64 {
    match bounds {
        (None, Some(hi)) => f64::from((point - hi).abs() < 1e-12),
        (Some(lo), None) => f64::from((point - lo).abs() < 1e-12),
        (Some(lo), Some(hi)) if point < lo || point > hi => 0.0,
        (Some(lo), Some(hi)) if (lo - hi).abs() < 1e-12 => 1.0,
        (Some(lo), Some(hi)) if (point - lo).abs() < 1e-12 => {
            ((value - lo) / (hi - lo) * std::f64::consts::FRAC_PI_2).cos()
        }
        (Some(lo), Some(hi)) => ((value - lo) / (hi - lo) * std::f64::consts::FRAC_PI_2).sin(),
        _ => 0.0,
    }
}

fn point_gains(
    points: &[[f64; 3]],
    xs: &[f64],
    ys: &[f64],
    zs: &[f64],
) -> (Vec<Vec<f64>>, Vec<Vec<f64>>, Vec<Vec<f64>>) {
    let mut gx = Vec::with_capacity(points.len());
    let mut gy = Vec::with_capacity(points.len());
    let mut gz = Vec::with_capacity(points.len());
    for point in points {
        gz.push(
            zs.iter()
                .map(|&value| {
                    balance_gain(point[2], value, bounds(points.iter().map(|p| p[2]), value))
                })
                .collect(),
        );
        gy.push(
            ys.iter()
                .map(|&value| {
                    balance_gain(
                        point[1],
                        value,
                        bounds(
                            points
                                .iter()
                                .filter(|p| (p[2] - point[2]).abs() < 1e-12)
                                .map(|p| p[1]),
                            value,
                        ),
                    )
                })
                .collect(),
        );
        gx.push(
            xs.iter()
                .map(|&value| {
                    balance_gain(
                        point[0],
                        value,
                        bounds(
                            points
                                .iter()
                                .filter(|p| {
                                    (p[1] - point[1]).abs() < 1e-12
                                        && (p[2] - point[2]).abs() < 1e-12
                                })
                                .map(|p| p[0]),
                            value,
                        ),
                    )
                })
                .collect(),
        );
    }
    (gx, gy, gz)
}

fn point(points: &[[f64; 3]], position: [f64; 3]) -> Vec<f64> {
    let (gx, gy, gz) = point_gains(points, &[position[0]], &[position[1]], &[position[2]]);
    points
        .iter()
        .enumerate()
        .map(|(index, _)| gx[index][0] * gy[index][0] * gz[index][0])
        .collect()
}
fn normalize(values: &[f64]) -> Vec<f64> {
    let norm = values.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm > 1e-16 {
        values.iter().map(|v| v / norm).collect()
    } else {
        vec![0.0; values.len()]
    }
}

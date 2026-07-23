import * as React from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { StemScene } from "@/api";

function position(azimuth: number, elevation: number): [number, number, number] {
  const az = azimuth * Math.PI / 180;
  const el = elevation * Math.PI / 180;
  return [-Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el)];
}

function Stem({ value, color, selected, onSelect, onMove }: { value: { azimuth_deg?: number; elevation_deg?: number }; color: string; selected: boolean; onSelect: () => void; onMove: (azimuth: number, elevation: number) => void }) {
  const [dragging, setDragging] = React.useState(false);
  const update = (event: ThreeEvent<PointerEvent>) => {
    if (!dragging) return;
    const point = event.point.normalize();
    const azimuth = Math.atan2(-point.x, -point.z) * 180 / Math.PI;
    const elevation = Math.max(0, Math.min(60, Math.asin(point.y) * 180 / Math.PI));
    onMove(azimuth, elevation);
  };
  return <mesh position={position(value.azimuth_deg || 0, value.elevation_deg || 0)} onPointerDown={(event) => { event.stopPropagation(); setDragging(true); onSelect(); }} onPointerUp={() => setDragging(false)} onPointerMove={update}>
    <sphereGeometry args={[0.08, 24, 24]} /><meshStandardMaterial color={selected ? "#60a5fa" : color} emissive={selected ? "#2563eb" : color} emissiveIntensity={0.35} />
  </mesh>;
}

export function SpatialScene({ stems, colors, selectedStem, onSelectStem, onChange }: { stems: StemScene; colors: Record<string, string>; selectedStem: string | null; onSelectStem: (stem: string) => void; onChange: (stem: string, azimuth: number, elevation: number) => void }) {
  return <div className="h-[380px] overflow-hidden rounded-lg border bg-slate-950"><Canvas camera={{ position: [2.4, 1.8, 2.4], fov: 50 }} onPointerMissed={() => onSelectStem("")}>
    <ambientLight intensity={1.2} /><pointLight position={[2, 3, 2]} intensity={15} />
    <gridHelper args={[3, 12, "#334155", "#1e293b"]} />
    <mesh position={[0, 0, 0]}><sphereGeometry args={[0.12, 20, 20]} /><meshStandardMaterial color="#e2e8f0" /></mesh>
    <mesh><sphereGeometry args={[1.25, 32, 24]} /><meshBasicMaterial color="#0f172a" wireframe transparent opacity={0.45} /></mesh>
    {Object.entries(stems).map(([name, value]) => <Stem key={name} value={value} color={colors[name] || "#94a3b8"} selected={selectedStem === name} onSelect={() => onSelectStem(name)} onMove={(azimuth, elevation) => onChange(name, azimuth, elevation)} />)}
    <OrbitControls enablePan={false} minDistance={2.5} maxDistance={4.5} />
  </Canvas></div>;
}

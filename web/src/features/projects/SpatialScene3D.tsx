import * as React from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Line, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { StemRouting } from "@/api";
import { speakerCoordinates, speakers, stemPosition, stemPositionStereo, type Vec3 } from "@/lib/spatial";

// World-space radius the unit speaker sphere is drawn at. Everything below
// (halos, guide lines, grid) is scaled against this one constant.
const SCALE = 3;

const haloTextureCache = new Map<string, THREE.CanvasTexture>();
function haloTexture(color: string): THREE.CanvasTexture {
  const cached = haloTextureCache.get(color);
  if (cached) return cached;
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, `${color}ff`);
    gradient.addColorStop(0.35, `${color}aa`);
    gradient.addColorStop(1, `${color}00`);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  const texture = new THREE.CanvasTexture(canvas);
  haloTextureCache.set(color, texture);
  return texture;
}

const labelTextureCache = new Map<string, THREE.CanvasTexture>();
function labelTexture(text: string, color = "#e2e8f0"): THREE.CanvasTexture {
  const key = `${text}|${color}`;
  const cached = labelTextureCache.get(key);
  if (cached) return cached;
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 48;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.font = "600 28px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = color;
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  }
  const texture = new THREE.CanvasTexture(canvas);
  labelTextureCache.set(key, texture);
  return texture;
}

function Label({ text, position, size = 0.6, color }: { text: string; position: [number, number, number]; size?: number; color?: string }) {
  const texture = React.useMemo(() => labelTexture(text, color), [text, color]);
  return <sprite position={position} scale={[size, size / 2.4, 1]}>
    <spriteMaterial map={texture} depthWrite={false} transparent />
  </sprite>;
}

function SpeakerMarker({ channel }: { channel: string }) {
  const anchor = speakerCoordinates[channel];
  const label = speakers[channel]?.label || channel;
  if (!anchor) return null;
  const position: [number, number, number] = [anchor.x * SCALE, anchor.y * SCALE, anchor.z * SCALE];
  return <group>
    {anchor.y > 0 && <Line points={[[position[0], 0, position[2]], position]} color="#334155" lineWidth={1} dashed dashSize={0.08} gapSize={0.08} />}
    <mesh position={position}>
      <sphereGeometry args={[anchor.y > 0 ? 0.11 : 0.09, 12, 12]} />
      <meshStandardMaterial color={anchor.y > 0 ? "#475569" : "#334155"} emissive="#1e293b" emissiveIntensity={0.6} />
    </mesh>
    <Label text={label} position={[position[0], position[1] + 0.32, position[2]]} size={0.5} />
  </group>;
}

function SendLines({ route, color }: { route: Record<string, number>; color: string }) {
  const max = Math.max(0.001, ...Object.values(route));
  return <>
    {Object.entries(route).map(([channel, weight]) => {
      if (weight <= 0 || channel === "LFE") return null;
      const anchor = speakerCoordinates[channel];
      if (!anchor) return null;
      const target: [number, number, number] = [anchor.x * SCALE, anchor.y * SCALE, anchor.z * SCALE];
      return <Line
        key={channel}
        points={[[0, 0, 0], target]}
        color={color}
        transparent
        opacity={0.25 + 0.55 * (weight / max)}
        lineWidth={1 + 2 * (weight / max)}
      />;
    })}
  </>;
}

// One rendered voice (either the single mono position, or one side of a
// stereo pair). Reads its level straight from the shared `levels` ref every
// frame — no React state in the render loop. `sizeScale` shrinks each half
// of a stereo pair a bit so the two together don't read as louder/bigger
// than a mono stem carrying the same level.
function HaloVoice({
  position,
  color,
  selected,
  dimmed,
  base,
  levels,
  sizeScale,
  onSelect,
}: {
  position: Vec3;
  color: string;
  selected: boolean;
  dimmed: boolean;
  base: string;
  levels: React.MutableRefObject<Map<string, number>>;
  sizeScale: number;
  onSelect: () => void;
}) {
  const group = React.useRef<THREE.Group>(null);
  const halo = React.useRef<THREE.Sprite>(null);
  const core = React.useRef<THREE.Mesh>(null);
  const coreMaterial = React.useRef<THREE.MeshStandardMaterial>(null);
  const target = React.useRef(new THREE.Vector3());
  const texture = React.useMemo(() => haloTexture(color), [color]);

  React.useEffect(() => {
    target.current.set(position.x * SCALE, position.y * SCALE, position.z * SCALE);
  }, [position.x, position.y, position.z]);

  useFrame((_, delta) => {
    if (group.current) group.current.position.lerp(target.current, Math.min(1, delta * 6));
    const level = levels.current.get(base) ?? 0;
    const emphasis = selected ? 1 : dimmed ? 0.35 : 0.75;
    const targetScale = (0.55 + level * 1.6) * (selected ? 1.15 : 1) * sizeScale;
    if (halo.current) {
      const current = halo.current.scale.x;
      const next = current + (targetScale - current) * Math.min(1, delta * 8);
      halo.current.scale.set(next, next, 1);
      const material = halo.current.material as THREE.SpriteMaterial;
      material.opacity = (0.35 + level * 0.65) * emphasis;
    }
    if (core.current) {
      const coreScale = (0.09 + level * 0.1) * sizeScale;
      core.current.scale.set(coreScale, coreScale, coreScale);
    }
    if (coreMaterial.current) {
      coreMaterial.current.emissiveIntensity = (0.6 + level * 2.4) * emphasis;
    }
  });

  return <group ref={group} onPointerDown={(event) => { event.stopPropagation(); onSelect(); }}>
    <sprite ref={halo} scale={[0.55, 0.55, 1]}>
      <spriteMaterial map={texture} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </sprite>
    <mesh ref={core}>
      <sphereGeometry args={[0.1, 16, 16]} />
      <meshStandardMaterial ref={coreMaterial} color={color} emissive={color} emissiveIntensity={0.6} toneMapped={false} />
    </mesh>
  </group>;
}

function StemHalo({
  stem,
  route,
  color,
  stereo,
  selected,
  dimmed,
  levels,
  onSelect,
}: {
  stem: string;
  route: Record<string, number>;
  color: string;
  stereo: boolean;
  selected: boolean;
  dimmed: boolean;
  levels: React.MutableRefObject<Map<string, number>>;
  onSelect: () => void;
}) {
  const base = stem.split("@", 1)[0];
  const monoPosition = React.useMemo(() => stemPosition(route), [route]);
  const stereoPosition = React.useMemo(() => stemPositionStereo(route), [route]);
  return <group onPointerDown={(event) => { event.stopPropagation(); onSelect(); }}>
    {stereo ? <>
      <HaloVoice position={stereoPosition.left} color={color} selected={selected} dimmed={dimmed} base={base} levels={levels} sizeScale={0.8} onSelect={onSelect} />
      <HaloVoice position={stereoPosition.right} color={color} selected={selected} dimmed={dimmed} base={base} levels={levels} sizeScale={0.8} onSelect={onSelect} />
    </> : (
      <HaloVoice position={monoPosition} color={color} selected={selected} dimmed={dimmed} base={base} levels={levels} sizeScale={1} onSelect={onSelect} />
    )}
    {selected && <SendLines route={route} color={color} />}
  </group>;
}

function Scene({
  channels,
  routing,
  selectedStem,
  colors,
  channelCounts,
  onSelectStem,
  stemLevels,
  playing,
}: SpatialScene3DProps) {
  const { gl } = useThree();
  React.useEffect(() => { gl.setClearColor(0x020617, 1); }, [gl]);
  const floorChannels = channels.filter((channel) => channel !== "LFE" && speakerCoordinates[channel]);
  const stems = Object.keys(routing);
  return <>
    <ambientLight intensity={0.7} />
    <pointLight position={[0, 4, 2]} intensity={40} />
    <Grid
      args={[SCALE * 2.6, SCALE * 2.6]}
      cellColor="#1e293b"
      sectionColor="#334155"
      fadeDistance={SCALE * 3}
      fadeStrength={1.5}
      infiniteGrid={false}
    />
    <mesh position={[0, 0, 0]}>
      <sphereGeometry args={[0.07, 12, 12]} />
      <meshStandardMaterial color="#e2e8f0" />
    </mesh>
    <Label text="FRONT" position={[0, 0.02, -SCALE * 1.2]} size={0.7} color="#94a3b8" />
    <Label text="BACK" position={[0, 0.02, SCALE * 1.2]} size={0.7} color="#94a3b8" />
    {floorChannels.map((channel) => <SpeakerMarker key={channel} channel={channel} />)}
    {stems.map((stem) => <StemHalo
      key={stem}
      stem={stem}
      route={routing[stem] || {}}
      color={colors[stem] || "#60a5fa"}
      stereo={(channelCounts?.[stem] ?? 2) >= 2}
      selected={selectedStem === stem}
      dimmed={Boolean(selectedStem) && selectedStem !== stem}
      levels={stemLevels}
      onSelect={() => onSelectStem(selectedStem === stem ? null : stem)}
    />)}
    <OrbitControls
      makeDefault
      enablePan={false}
      minDistance={SCALE * 1.4}
      maxDistance={SCALE * 5}
      maxPolarAngle={Math.PI * 0.49}
      autoRotate={!playing}
      autoRotateSpeed={0.6}
    />
  </>;
}

export type SpatialScene3DProps = {
  channels: string[];
  routing: StemRouting;
  selectedStem: string | null;
  colors: Record<string, string>;
  // Base stem name -> source channel count. Stems with 2+ channels get an
  // L/R halo pair instead of one collapsed to a single point; unknown stems
  // default to stereo (separated stems are stereo far more often than not).
  channelCounts?: Record<string, number>;
  onSelectStem: (stem: string | null) => void;
  stemLevels: React.MutableRefObject<Map<string, number>>;
  playing: boolean;
  className?: string;
};

export default function SpatialScene3D({ className, onSelectStem, selectedStem, routing, channels, ...rest }: SpatialScene3DProps) {
  const lfeRoute = selectedStem ? routing[selectedStem]?.LFE || 0 : 0;
  return <div className={`relative flex flex-col overflow-hidden rounded-lg border bg-slate-950 text-slate-100 ${className || ""}`}>
    <div className="pointer-events-none relative z-10 mb-0 flex items-center justify-between px-3 pt-3 text-xs text-slate-300">
      <span>Speaker routing</span>
      <button className="pointer-events-auto hover:text-white" onClick={() => onSelectStem(null)}>{selectedStem || "Aggregate output"}</button>
    </div>
    <div className="min-h-0 flex-1">
      <Canvas
        camera={{ position: [0, SCALE * 1.15, SCALE * 1.9], fov: 45 }}
        onPointerMissed={() => onSelectStem(null)}
      >
        <Scene {...rest} channels={channels} routing={routing} selectedStem={selectedStem} onSelectStem={onSelectStem} />
      </Canvas>
    </div>
    {channels.includes("LFE") && <div className="pointer-events-none absolute bottom-2 left-2 z-10 rounded bg-slate-800/90 px-2 py-1 text-xs">LFE {selectedStem ? `${lfeRoute.toFixed(2)} send` : "bus"}</div>}
  </div>;
}

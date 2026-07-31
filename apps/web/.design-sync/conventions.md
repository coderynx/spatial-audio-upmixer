# Upmixer Design System — how to build with it

A React + Tailwind component kit (shadcn/New-York base, Radix primitives) for a
pro-audio app: an Apple/Logic-Pro-flavoured surface of compact controls, mixer
faders, rotary pots, and stem toggles. Every component is imported from
`window.UpmixerWeb` and rendered directly — there is no reimplementation.

## Setup — no provider needed

Components are self-contained. There is **no theme/context provider to wrap the
app in** — render any component directly. Radix-based ones (Dialog, Select,
Switch, Slider, Tabs, Label) manage their own context internally. Dark mode is
driven by a `class="dark"` on an ancestor (Tailwind `darkMode: ["class"]`).

## Styling idiom — Tailwind utilities over semantic token colours

Style with **Tailwind utility classes**; never hand-write CSS or invent colour
values. Colours are semantic CSS-variable tokens (defined as `--primary` etc.,
consumed as `hsl(var(--…))`), exposed as these utility families — use them, not
raw palette colours:

| Token family | Utilities |
|---|---|
| primary (accent/CTA) | `bg-primary` `text-primary-foreground` `text-primary` |
| secondary (fields, chips) | `bg-secondary` `text-secondary-foreground` |
| destructive / success / warning | `bg-destructive` `bg-success` `bg-warning` (+ `-foreground`) |
| surfaces | `bg-background` `bg-card` `bg-popover` `bg-muted` `bg-accent` |
| text | `text-foreground` `text-muted-foreground` |
| lines / controls | `border` `border-input` `ring-ring` |

Layout with normal Tailwind utilities (`flex`, `grid`, `gap-*`, `space-y-*`,
`rounded-md`, `text-[13px]`). The surface is **compact** — default controls are
`h-7` with `text-[13px]`; keep new layout glue at that density. `Button` and
`Badge` carry a `variant` prop (`default`/`secondary`/`outline`/`ghost`/`link`/
`destructive`/`success`/`warning`); `Button` also has `size`
(`sm`/`default`/`lg`/`icon`).

## Where the truth lives

- **`styles.css`** (and its `@import` of `_ds_bundle.css`) holds every token and
  compiled utility — read it before styling to confirm a class/token exists.
- **Per-component `<Name>.d.ts` + `<Name>.prompt.md`** are the API + usage for
  each component. Compound components (Card, Dialog, Select, Tabs) are composed
  from their parts — e.g. `Card`+`CardHeader`+`CardTitle`+`CardContent`+
  `CardFooter`; `Select`+`SelectTrigger`+`SelectValue`+`SelectContent`+
  `SelectItem`. Form-field wrappers (`SwitchRow`, `SelectField`, `SliderField`,
  `NumberField`, `ToggleField`, `NullablePotField`) bundle a `Label` + control.
- **Audio controls** (`Fader`, `HorizontalFader`, `Pot`) are controlled: pass
  `value` + `onChange` (+ `valueText` for the accessible readout). `Fader` has no
  intrinsic height — give it a sized parent and `className="h-full"`.

## Idiomatic snippet

```tsx
import { Card, CardHeader, CardTitle, CardContent, Label, Input, Button } from "upmixer-web";

<Card className="w-80">
  <CardHeader>
    <CardTitle>Export master</CardTitle>
  </CardHeader>
  <CardContent className="space-y-3">
    <div className="space-y-1.5">
      <Label htmlFor="lufs">Target loudness</Label>
      <Input id="lufs" defaultValue="-14 LUFS" />
    </div>
    <Button className="w-full">Render</Button>
  </CardContent>
</Card>
```

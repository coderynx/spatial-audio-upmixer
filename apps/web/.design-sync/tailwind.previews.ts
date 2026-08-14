import base from "../tailwind.config";

// design-sync: compile the app's Tailwind PLUS the authored preview cards so
// utilities used only in .design-sync/previews/ (e.g. h-56) are emitted into
// the shipped compiled.css.
export default {
  ...base,
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "./.design-sync/previews/**/*.tsx",
  ],
};

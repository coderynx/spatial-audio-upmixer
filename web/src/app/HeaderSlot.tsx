import * as React from "react";

const HeaderSlotContext = React.createContext<{
  node: React.ReactNode;
  setNode: (node: React.ReactNode) => void;
} | null>(null);

export function HeaderSlotProvider({ children }: { children: React.ReactNode }) {
  const [node, setNode] = React.useState<React.ReactNode>(null);
  const value = React.useMemo(() => ({ node, setNode }), [node]);
  return <HeaderSlotContext.Provider value={value}>{children}</HeaderSlotContext.Provider>;
}

export function useHeaderSlot() {
  const context = React.useContext(HeaderSlotContext);
  if (!context) throw new Error("useHeaderSlot must be used within HeaderSlotProvider");
  return context;
}

/** `node` must be referentially stable (e.g. via `useMemo`) across renders
 * that don't actually change it — this effect re-fires whenever `node`'s
 * identity changes, which updates provider state and re-renders the caller,
 * so a fresh element passed in on every render loops forever. */
export function useHeaderTitle(node: React.ReactNode) {
  const { setNode } = useHeaderSlot();
  React.useEffect(() => {
    setNode(node);
    return () => setNode(null);
  }, [node, setNode]);
}

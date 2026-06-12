import { useEffect, useRef, useState } from "react";

// Easing matches the original animate.js (cubic ease-out, 350ms).
const DURATION_MS = 350;
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export function useAnimatedNumber(value: number, duration = DURATION_MS): number {
  const [displayed, setDisplayed] = useState(value);
  const displayedRef = useRef(value);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (displayedRef.current === value) return;
    const from = displayedRef.current;
    const start = performance.now();
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    function step(now: number) {
      const t = Math.min(1, (now - start) / duration);
      const v = from + (value - from) * easeOut(t);
      displayedRef.current = v;
      setDisplayed(v);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        displayedRef.current = value;
        setDisplayed(value);
        rafRef.current = null;
      }
    }
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [value, duration]);

  return displayed;
}

type AnimatedUsdProps = { value: number; decimals?: number };

export function AnimatedUsd({ value, decimals = 0 }: AnimatedUsdProps) {
  const displayed = useAnimatedNumber(value);
  return (
    <>
      {displayed.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
    </>
  );
}

export function AnimatedCount({ value }: { value: number }) {
  const displayed = useAnimatedNumber(value);
  return <>{Math.round(displayed).toLocaleString("en-US")}</>;
}

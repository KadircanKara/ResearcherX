"use client";
import { useEffect, useState } from "react";
import { Rows3, Rows2 } from "lucide-react";
import { Button } from "@/components/ui/button";

type Density = "comfortable" | "compact";

const STORAGE_KEY = "rx-density";

function applyDensity(density: Density) {
  document.documentElement.dataset.density = density;
}

export function DensityToggle() {
  const [density, setDensity] = useState<Density>("comfortable");

  // On mount, read persisted value and apply
  useEffect(() => {
    const saved = (localStorage.getItem(STORAGE_KEY) as Density | null) ?? "comfortable";
    setDensity(saved);
    applyDensity(saved);
  }, []);

  function toggle() {
    const next: Density = density === "comfortable" ? "compact" : "comfortable";
    setDensity(next);
    applyDensity(next);
    localStorage.setItem(STORAGE_KEY, next);
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${density === "comfortable" ? "compact" : "comfortable"} density`}
      onClick={toggle}
      className="size-8 text-muted-foreground hover:text-foreground"
    >
      {density === "comfortable" ? (
        <Rows3 className="size-4" />
      ) : (
        <Rows2 className="size-4" />
      )}
    </Button>
  );
}

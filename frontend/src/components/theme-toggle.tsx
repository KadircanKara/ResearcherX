"use client";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * The prototype's theme toggle -- a ghost round icon button with a tooltip --
 * driving next-themes rather than flipping the `dark` class by hand, so the
 * choice persists and the pre-hydration script applies it before first paint.
 *
 * `mounted` gates the icon: the server cannot know the stored theme, so the
 * first render always shows the light-theme state (as the prototype's does)
 * and the real one lands in the effect, with no hydration mismatch.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const dark = mounted && resolvedTheme === "dark";

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={() => setTheme(dark ? "light" : "dark")}
            aria-label={dark ? "Use light theme" : "Use dark theme"}
          />
        }
      >
        {dark ? <Sun /> : <Moon />}
      </TooltipTrigger>
      <TooltipContent>{dark ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  );
}

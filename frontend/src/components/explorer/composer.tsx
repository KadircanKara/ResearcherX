"use client";

import { useId, useState } from "react";

/**
 * The Explorer composer.
 *
 * Explorer is a mock view: there is no discovery backend, so the submit control
 * is genuinely disabled and says so. It is deliberately NOT a live-looking
 * button that silently does nothing — a control that lies about what it will do
 * is worse than one that admits it is a preview. The textarea is real, because
 * what the user types is their own.
 */
export function Composer({
  label,
  placeholder,
  submitLabel,
}: {
  label: string;
  placeholder: string;
  submitLabel: string;
}) {
  const id = useId();
  const [value, setValue] = useState("");

  return (
    <div className="rx-composer">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <textarea
        id={id}
        rows={2}
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="rx-bar">
        <span>Design preview — Explorer has no discovery backend yet.</span>
        <button type="button" className="rx-btn rx-push" disabled>
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

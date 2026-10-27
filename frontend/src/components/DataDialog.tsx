import { useEffect, useId, useRef } from "react";

type Props = {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  text?: { value: string; onChange: (value: string) => void };
};

export default function DataDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  text,
}: Props) {
  const headingId = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>(text ? "input" : "button")?.focus();
    return () => previous?.focus();
  }, []);
  return (
    <div className="dialog-backdrop">
      <div
        ref={box}
        className="data-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          }
          if (event.key === "Tab") {
            const controls = [
              ...(box.current?.querySelectorAll<HTMLElement>(
                "input, button:not(:disabled)",
              ) || []),
            ];
            const first = controls[0],
              last = controls.at(-1);
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <h2 id={headingId}>{title}</h2>
        <p>{message}</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onConfirm();
          }}
        >
          {text && (
            <label>
              Conversation title
              <input
                required
                maxLength={100}
                value={text.value}
                onChange={(event) => text.onChange(event.target.value)}
              />
            </label>
          )}
          <div className="record-actions">
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" disabled={text ? !text.value.trim() : false}>
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

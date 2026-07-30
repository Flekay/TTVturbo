import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

interface CanvasTooltipProps {
  title: string;
  description: string;
  shortcut?: string;
  children: ReactNode;
}

export function CanvasTooltip({ title, description, shortcut, children }: CanvasTooltipProps) {
  return (
    <TooltipPrimitive.Provider delayDuration={400}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content side="right" sideOffset={8} className="canvas-tooltip">
            <div className="canvas-tooltip__header">
              <strong>{title}</strong>
              {shortcut ? <kbd className="canvas-tooltip__shortcut">{shortcut}</kbd> : null}
            </div>
            <p className="canvas-tooltip__description">{description}</p>
            <TooltipPrimitive.Arrow className="canvas-tooltip__arrow" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

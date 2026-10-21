import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PttButton } from "./PttButton";

afterEach(cleanup);

function setup() {
  const onStart = vi.fn();
  const onEnd = vi.fn();
  const props = { phase: "IDLE" as const, label: "PTT", hint: "HOLD", onStart, onEnd };
  const view = render(<PttButton {...props} />);
  const button = screen.getByRole("button", { name: "PTT" });
  const pointer = (type: string, pointerId = 4) => {
    const event = new MouseEvent(type, { bubbles: true, button: 0 });
    Object.defineProperty(event, "pointerId", { value: pointerId });
    fireEvent(button, event);
  };
  return { ...view, onStart, onEnd, props, button, pointer };
}

describe("PttButton", () => {
  it.each(["pointerup", "pointercancel", "lostpointercapture"])("releases on %s even before TX is acknowledged", (type) => {
    const { pointer, button, onStart, onEnd } = setup();
    const capture = vi.fn();
    button.setPointerCapture = capture;
    pointer("pointerdown");
    pointer(type);
    pointer("pointerup");
    expect(capture).toHaveBeenCalledWith(4);
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it("keys a focused button with Space/Enter, ignores repeats, and releases on blur", () => {
    const { button, onStart, onEnd } = setup();
    fireEvent.keyDown(button, { code: "Space" });
    fireEvent.keyDown(button, { code: "Space", repeat: true });
    fireEvent.blur(button);
    fireEvent.keyUp(button, { code: "Space" });
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(button, { code: "Enter" });
    fireEvent.keyUp(button, { code: "Enter" });
    expect(onStart).toHaveBeenCalledTimes(2);
    expect(onEnd).toHaveBeenCalledTimes(2);
  });

  it("resets pointer state on window blur so the next press still works", () => {
    const { pointer, onStart, onEnd } = setup();
    pointer("pointerdown");
    fireEvent(window, new Event("blur"));
    pointer("pointerdown");
    pointer("pointerup");
    expect(onStart).toHaveBeenCalledTimes(2);
    expect(onEnd).toHaveBeenCalledTimes(2);
  });

  it("releases when disabled or removed and cannot start disabled", () => {
    const { pointer, props, rerender, unmount, onStart, onEnd } = setup();
    pointer("pointerdown");
    rerender(<PttButton {...props} disabled />);
    pointer("pointerdown");
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledTimes(1);
    rerender(<PttButton {...props} />);
    pointer("pointerdown");
    unmount();
    expect(onEnd).toHaveBeenCalledTimes(2);
  });
});

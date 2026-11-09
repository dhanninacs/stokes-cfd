/**
 * Where Appearance hangs from: the View island's button while that island is shown, else a
 * fixed point at the bottom-right of the viewport (the island can be hidden; `Ctrl ,` and the palette
 * still open the popover). A module of its own so the island and the popover need not import each other.
 */

let button: HTMLElement | null = null;

export function setDisplayAnchor(el: HTMLElement | null): void {
  button = el;
}

export function displayAnchorButton(): HTMLElement | null {
  return button?.isConnected ? button : null;
}

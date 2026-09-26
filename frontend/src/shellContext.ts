type Listener = (text: string) => void;

let current = "";
const listeners = new Set<Listener>();

export function setHeaderContext(text: string) {
  current = text;
  listeners.forEach((fn) => fn(text));
}

export function subscribeHeaderContext(fn: Listener) {
  listeners.add(fn);
  fn(current);
  return () => {
    listeners.delete(fn);
  };
}

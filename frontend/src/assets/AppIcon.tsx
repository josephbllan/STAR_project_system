import { MouseEvent } from "react";
import aiBrain from "./icons/ai-brain.png";
import check from "./icons/check1.png";
import closeFile from "./icons/close_file.png";
import column3 from "./icons/column3.png";
import deleteIcon from "./icons/delete.png";
import displayNotes from "./icons/display_notes.png";
import folder from "./icons/folder.png";
import fullscreen from "./icons/fullscreen.png";
import grid from "./icons/grid1.png";
import linkFolder from "./icons/link_folder.png";
import linkWeb from "./icons/link_web1.png";
import list from "./icons/list.png";
import saveData from "./icons/save_data.png";
import search from "./icons/search.png";
import shoe from "./icons/shoe.png";
import star from "./icons/star.png";
import validation from "./icons/validation.png";
import warning from "./icons/warning.png";
import writeNote from "./icons/write_note.png";
import logo from "./logo/star.png";

export const ICONS = {
  aiBrain,
  check,
  closeFile,
  column3,
  delete: deleteIcon,
  displayNotes,
  folder,
  fullscreen,
  grid,
  linkFolder,
  linkWeb,
  list,
  saveData,
  search,
  shoe,
  star,
  validation,
  warning,
  writeNote,
} as const;

export const APP_LOGO = logo;

export type IconName = keyof typeof ICONS;

export function AppIcon({
  name,
  size = 32,
  alt = "",
  className = "",
}: {
  name: IconName;
  size?: number;
  alt?: string;
  className?: string;
}) {
  return (
    <img
      src={ICONS[name]}
      width={size}
      height={size}
      alt={alt}
      className={`shrink-0 object-contain ${className}`.trim()}
      draggable={false}
    />
  );
}

export function AppLogo({
  className = "",
  alt = "ShoeRAG",
}: {
  className?: string;
  alt?: string;
}) {
  return <img src={APP_LOGO} alt={alt} className={`object-contain ${className}`.trim()} draggable={false} />;
}

export function IconAction({
  name,
  label,
  size = 30,
  disabled,
  active,
  onClick,
}: {
  name: IconName;
  label: string;
  size?: number;
  disabled?: boolean;
  active?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active || undefined}
      disabled={disabled}
      onClick={onClick}
      className={[
        "inline-flex items-center justify-center rounded-full bg-transparent p-0",
        active ? "ring-2 ring-[var(--focus)] ring-offset-1" : "",
        disabled ? "cursor-not-allowed opacity-40" : "hover:opacity-90",
      ].join(" ")}
      style={{ width: size, height: size }}
    >
      <AppIcon name={name} size={size} alt="" />
    </button>
  );
}

const PATHS = {
  plus: 'M10 4v12M4 10h12',
  doc: 'M5 3h7l3 3v11H5zM12 3v3h3',
  gear: 'M10 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4',
  sidebar: 'M5 4h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM8 4v12',
  search: 'M9 4a5 5 0 1 0 0 10A5 5 0 0 0 9 4zM13 13l3.5 3.5',
  chevron: 'M6 8l4 4 4-4',
  check: 'M5 10.5l3 3 7-7',
  warn: 'M10 3l8 14H2zM10 8v4M10 14.5v.5',
  close: 'M5 5l10 10M15 5L5 15',
  trash: 'M4 6h12M8 6V4h4v2M6 6l1 11h6l1-11',
  copy: 'M9 7h7a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zM12 7V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3',
  retry: 'M4 10a6 6 0 1 0 2-4.5M4 3v3.5h3.5',
  up: 'M10 15V5M5.5 9.5L10 5l4.5 4.5',
  spark: 'M10 2.5l1.8 4.7 4.7 1.8-4.7 1.8L10 15.5l-1.8-4.7L3.5 9l4.7-1.8z',
  upload: 'M10 13V3M6 7l4-4 4 4M3 13v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3',
  key: 'M12.5 3.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM9.7 10.3L3 17M5 15l2 2M6.5 13.5l2 2',
  palette: 'M10 3a7 7 0 0 0 0 14c1 0 1.5-.6 1.5-1.4 0-1.2-1-1.3-1-2.4 0-.8.7-1.2 1.5-1.2H14a3 3 0 0 0 3-3C17 5.7 13.9 3 10 3zM6.5 9.5h.01M8.5 6.5h.01M12 6.5h.01',
  sliders: 'M4 6h8M15 6h1M4 14h2M9 14h7M12 4v4M6 12v4',
  external: 'M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3M11 4h5v5M16 4l-7 7',
  edit: 'M4 16h3l9-9-3-3-9 9zM12 5l3 3',
  terminal: 'M4 5l4 5-4 5M10 15h6',
  bolt: 'M11 2L4 11h5l-1 7 7-9h-5z',
  folder: 'M3 6a1 1 0 0 1 1-1h4l2 2h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z',
  shield: 'M10 2.5l6 2.5v4.5c0 3.8-2.6 6.6-6 8-3.4-1.4-6-4.2-6-8V5z',
  eye: 'M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10zM10 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z',
  eyeOff: 'M3 3l14 14M8.2 5a8 8 0 0 1 1.8-.5c5 0 8 5.5 8 5.5a14 14 0 0 1-2.3 2.9M5.3 6.4A14 14 0 0 0 2 10s3 5.5 8 5.5c1.3 0 2.5-.4 3.5-.9M8.3 8.3a2.5 2.5 0 0 0 3.4 3.4',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
    </svg>
  )
}

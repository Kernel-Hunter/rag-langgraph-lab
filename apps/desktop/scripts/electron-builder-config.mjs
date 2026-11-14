// electron-builder settings for the Vesper desktop app (unsigned builds for
// macOS arm64, Windows x64 and Linux x64).
//
// The engine folder is the PyInstaller onedir build of src/vesper, made by
// `python packaging/build_engine.py` before this runs.

import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const here = (relative) => fileURLToPath(new URL(relative, import.meta.url))
const icon = here('../../../packaging/icon.png')
const engineDir = here('../engine')

if (!existsSync(engineDir)) {
  throw new Error(`Vesper engine not found at ${engineDir}. Run: python packaging/build_engine.py`)
}

const only = (arch, ...targets) => targets.map((target) => ({ target, arch: [arch] }))

export default {
  appId: 'io.github.kernel-hunter.vesper',
  productName: 'Vesper',
  copyright: `Copyright © ${new Date().getFullYear()} Karim Masmoudi`,
  artifactName: 'Vesper-${version}-${os}-${arch}.${ext}',
  publish: null,
  directories: { output: 'release' },

  // The app code lives in an asar archive; the Python engine ships next to it.
  asar: true,
  files: ['dist/**/*', 'package.json'],
  extraResources: [{ from: engineDir, to: 'engine', filter: ['**/*'] }],

  // Lock down the Electron binary: it cannot run as plain Node, ignores inspector
  // flags and NODE_OPTIONS, and only loads the app from its checked asar.
  electronFuses: {
    runAsNode: false,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    onlyLoadAppFromAsar: true,
    enableEmbeddedAsarIntegrityValidation: true,
    // The app stores nothing in cookies, so there is nothing to encrypt with the OS keychain.
    enableCookieEncryption: false,
    grantFileProtocolExtraPrivileges: false,
  },

  mac: {
    icon,
    category: 'public.app-category.productivity',
    extendInfo: { NSMicrophoneUsageDescription: 'Vesper uses your microphone when you choose to dictate a message.' },
    target: only('arm64', 'dmg', 'zip'),
    // No Developer ID: an ad-hoc signature is enough for Apple Silicon to run it.
    identity: '-',
    hardenedRuntime: false,
    notarize: false,
  },
  dmg: { sign: false, writeUpdateInfo: false },

  win: { icon, target: only('x64', 'nsis') },
  // Per-user install with no admin prompt; the user may pick the folder.
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    createStartMenuShortcut: true,
    createDesktopShortcut: true,
    shortcutName: 'Vesper',
    uninstallDisplayName: 'Vesper',
    differentialPackage: false,
  },

  linux: {
    icon,
    category: 'Office',
    executableName: 'vesper',
    synopsis: 'AI agent for your folders and documents',
    target: only('x64', 'AppImage', 'tar.gz'),
  },
}

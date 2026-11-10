// electron-builder configuration for Pagewise.
// Adapted from apps/desktop/scripts/electron-builder-config.mjs in
// deepseek-ai/deepseek-harness (MIT, Copyright (c) 2026 DeepSeek), reduced to
// the unsigned build path; see THIRD_PARTY_NOTICES.md.
//
// The engine folder is the PyInstaller onedir build of src/rag_lab, made by
// `python packaging/build_engine.py` before this runs.

import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const icon = fileURLToPath(new URL('../../../packaging/icon.png', import.meta.url))
const engine = fileURLToPath(new URL('../engine', import.meta.url))

if (!existsSync(engine)) {
  throw new Error(`Pagewise engine not found at ${engine}. Run: python packaging/build_engine.py`)
}

export default {
  appId: 'io.github.kernel-hunter.pagewise',
  productName: 'Pagewise',
  copyright: `Copyright © ${new Date().getFullYear()} Karim Masmoudi`,
  artifactName: 'Pagewise-${version}-${os}-${arch}.${ext}',
  directories: { output: 'release' },
  asar: true,
  files: ['dist/**/*', 'package.json'],
  extraResources: [{ from: engine, to: 'engine', filter: ['**/*'] }],
  // Lock down the Electron binary: it cannot be run as plain Node, does not take
  // inspector flags or NODE_OPTIONS, and only loads the app from its checked asar.
  electronFuses: {
    runAsNode: false,
    enableCookieEncryption: true,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    enableEmbeddedAsarIntegrityValidation: true,
    onlyLoadAppFromAsar: true,
    grantFileProtocolExtraPrivileges: false,
  },
  mac: {
    icon,
    category: 'public.app-category.productivity',
    target: [{ target: 'dmg', arch: ['arm64'] }, { target: 'zip', arch: ['arm64'] }],
    // Ad-hoc signing keeps the app runnable on Apple Silicon without a Developer ID.
    identity: '-',
    hardenedRuntime: false,
    notarize: false,
  },
  dmg: {
    sign: false,
    writeUpdateInfo: false,
  },
  win: {
    icon,
    target: [{ target: 'nsis', arch: ['x64'] }],
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Pagewise',
    uninstallDisplayName: 'Pagewise',
    differentialPackage: false,
  },
  linux: {
    icon,
    category: 'Office',
    executableName: 'pagewise',
    synopsis: 'Chat with your documents',
    target: [{ target: 'AppImage', arch: ['x64'] }, { target: 'tar.gz', arch: ['x64'] }],
  },
  publish: null,
}

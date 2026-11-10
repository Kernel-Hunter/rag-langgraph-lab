# Third-party notices

Pagewise includes code adapted from the projects below. Their licenses apply
to those parts.

## deepseek-ai/deepseek-harness

https://github.com/deepseek-ai/deepseek-harness

Adapted in the Electron desktop shell:

- `apps/desktop/src/main.ts`: `createWindow()` (window options per platform,
  the macOS sidebar vibrancy setup, the window-open and navigation guards) and
  the edit context menu, from `apps/desktop/src/main.ts`.
- `apps/desktop/scripts/electron-builder-config.mjs`: the electron-builder
  configuration, reduced to the unsigned build path, from
  `apps/desktop/scripts/electron-builder-config.mjs`.

Pagewise is not affiliated with DeepSeek and uses none of its names or artwork.

```
MIT License

Copyright (c) 2026 DeepSeek

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Electron

The desktop app is built on Electron (MIT, Copyright (c) Electron contributors,
Copyright (c) 2013-2020 GitHub Inc.) and Chromium. Electron's packaging adds
their license files (`LICENSE.electron.txt`, `LICENSES.chromium.html`) to the
Windows and Linux builds; see https://github.com/electron/electron/blob/main/LICENSE.

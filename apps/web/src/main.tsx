import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './packages/ui-theme/tokens.css'
import './packages/ui-primitives/primitives.css'
import { App } from './App'
import { applyAppearance, loadAppearance } from './packages/ui-theme/appearance'

applyAppearance(loadAppearance())

const root = document.getElementById('root')
if (root) createRoot(root).render(<StrictMode><App /></StrictMode>)

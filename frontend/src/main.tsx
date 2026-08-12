import React from 'react';
import ReactDOM from 'react-dom/client';
// Self-hosted so the app has no runtime font-CDN dependency -- it's served behind
// Cloudflare Access and previously @import-ed these two faces from Google Fonts while
// --font-body/--font-display still pointed at system stacks, so the download was paid
// for and never used.
import '@fontsource-variable/inter-tight';
import '@fontsource-variable/jetbrains-mono';
import { App } from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);

// src/main.jsx (Versão com MUI ThemeProvider e CssBaseline)
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './index.css'; // Mantém Tailwind + Datepicker CSS
import { BrowserRouter } from "react-router-dom";

// --- NOVOS IMPORTS DO MUI ---
import { ThemeProvider, createTheme } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
// --- FIM DOS NOVOS IMPORTS ---

// Cria um tema básico. Podemos customizar isso depois.
// O modo 'dark' pode explicar o fundo escuro que você viu. Vamos forçar 'light' por enquanto.
const theme = createTheme({
  palette: {
    mode: 'light', // Força o modo claro para ter fundo branco por padrão
  },
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Envolve tudo com o ThemeProvider e CssBaseline */}
    <ThemeProvider theme={theme}>
      <CssBaseline /> {/* Aplica o reset de CSS */}
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
)
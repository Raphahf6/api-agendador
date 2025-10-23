// src/firebaseConfig.js
import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

// TODO: Adicione as variáveis de configuração do seu projeto Firebase aqui
// https://firebase.google.com/docs/web/setup#available-libraries
const firebaseConfig = {
    // <<< COLE SEU firebaseConfig AQUI >>>
  apiKey: "AIzaSyDnWiiKwscCwDBLQK-KUzXvLpK7KNvjOEo",
  authDomain: "agendador-bot.firebaseapp.com",
  projectId: "agendador-bot",
  storageBucket: "agendador-bot.firebasestorage.app",
  messagingSenderId: "608445560721",
  appId: "1:608445560721:web:3e4699e5497f47c8a58c30",
  measurementId: "G-2J6M066F72"
};


// Inicializa o Firebase
const app = initializeApp(firebaseConfig);

// Exporta a instância de autenticação para ser usada em outros lugares
export const auth = getAuth(app);

export default app; // Exporta a app inicializada se precisar de outros serviços
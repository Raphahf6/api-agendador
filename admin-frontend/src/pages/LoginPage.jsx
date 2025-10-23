// src/pages/LoginPage.jsx (Versão CORRIGIDA sem MUI Container)
import React, { useState } from 'react';
import { signInWithEmailAndPassword } from "firebase/auth";
import { auth } from '../firebaseConfig'; 
import { Box, Button, TextField, Typography, Alert, Paper } from '@mui/material'; // Adicionado Paper

function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault(); 
    setError('');
    setLoading(true);
    try {
      await signInWithEmailAndPassword(auth, email, password);
      // Sucesso, App.jsx redireciona
    } catch (err) {
      console.error("Erro no login:", err);
      setError("Falha no login. Verifique seu e-mail e senha.");
    } finally {
      setLoading(false);
    }
  };

  return (
    // Usamos Paper para dar um fundo branco e sombra ao formulário
    <Paper elevation={3} sx={{ padding: 4, display: 'flex', flexDirection: 'column', alignItems: 'center', maxWidth: '400px', width: '90%' }}> 
      <Typography component="h1" variant="h5" sx={{ marginBottom: 2 }}>
        Admin Login
      </Typography>
      <Box component="form" onSubmit={handleLogin} noValidate sx={{ width: '100%' }}> {/* Formulário ocupa 100% do Paper */}
        <TextField
          margin="normal"
          required
          fullWidth
          id="email"
          label="Endereço de E-mail"
          name="email"
          autoComplete="email"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={loading}
        />
        <TextField
          margin="normal"
          required
          fullWidth
          name="password"
          label="Senha"
          type="password"
          id="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={loading}
        />
        {error && <Alert severity="error" sx={{ mt: 2, width: '100%' }}>{error}</Alert>}
        <Button
          type="submit"
          fullWidth
          variant="contained"
          sx={{ mt: 3, mb: 2 }}
          disabled={loading}
        >
          {loading ? 'Entrando...' : 'Entrar'}
        </Button>
      </Box>
    </Paper>
  );
}

export default LoginPage;
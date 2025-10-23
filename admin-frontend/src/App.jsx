// src/App.jsx (Versão FINAL E CORRIGIDA com Layout MUI Consistente)
import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, Link as RouterLink } from 'react-router-dom';
import { onAuthStateChanged, signOut } from "firebase/auth";
import { auth } from './firebaseConfig'; 
import LoginPage from './pages/LoginPage';
import { Button, Typography, Box, CircularProgress, Container, AppBar, Toolbar, Link } from '@mui/material'; // Imports adicionados
import ClientListPage from './pages/ClientListPage';
import AddClientPage from './pages/AddClientPage';
import EditClientPage from './pages/EditClientPage';

// Placeholder para a página principal do admin
function AdminDashboard({ user, onLogout }) {
  // Usamos Container aqui também para consistência no layout interno
  return (
    <Container component="main" maxWidth="md" sx={{ mt: 4, mb: 4 }}> 
      <Box sx={{ p: 3, textAlign: 'center', bgcolor: 'background.paper', borderRadius: 1, boxShadow: 3 }}>
         <Typography variant="h4" gutterBottom>Bem-vindo, Admin!</Typography>
         <Typography variant="body1" gutterBottom>Seu e-mail: {user.email}</Typography>
         <Link component={RouterLink} to="/clientes" variant="body1" sx={{ display: 'block', mt: 2 }}>
            Ver Lista de Clientes
         </Link>
         <Button variant="contained" color="secondary" onClick={onLogout} sx={{ mt: 2 }}>
           Sair
         </Button>
      </Box>
    </Container>
  );
}

function App() {
  const [user, setUser] = useState(null); 
  const [loadingAuth, setLoadingAuth] = useState(true); 
  const navigate = useNavigate();

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      setUser(currentUser);
      setLoadingAuth(false);
    });
    return () => unsubscribe();
  }, []);

  const handleLogout = async () => {
    try {
      await signOut(auth);
      navigate('/login'); 
    } catch (error) {
      console.error("Erro ao sair:", error);
    }
  };

  // Tela de carregamento centralizada
  if (loadingAuth) {
    return (
        <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
            <CircularProgress />
        </Box>
    );
  }

  // Se o usuário estiver logado, mostramos um layout com AppBar
  if (user) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
         <AppBar position="static">
            <Toolbar>
                <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
                    Painel Admin - Agendador
                </Typography>
                {/* Botão Sair fica na barra superior */}
            </Toolbar>
         </AppBar>
         {/* O conteúdo principal (Dashboard) */}
         <Routes>
            <Route path="/" element={<AdminDashboard user={user} onLogout={handleLogout} />} />
            <Route path="/clientes" element={<ClientListPage />} />
            <Route path="/clientes/novo" element={<AddClientPage />} />
            <Route path="/clientes/editar/:clientId" element={<EditClientPage />} />
            <Route path="*" element={<Navigate to="/" replace />} /> {/* Redireciona tudo para o dashboard se logado */}
         </Routes>
      </Box>
    );
  }

  // Se não estiver logado, mostramos a tela de Login centralizada
  return (
    <Container component="main" maxWidth={false} // Ocupa largura total
      sx={{ 
        display: 'flex', 
        minHeight: '100vh', 
        alignItems: 'center', 
        justifyContent: 'center',
        bgcolor: 'grey.100' // Fundo cinza claro
      }}
    >
      <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/login" replace />} /> {/* Redireciona tudo para login se não logado */}
      </Routes>
    </Container>
  );
}

export default App;
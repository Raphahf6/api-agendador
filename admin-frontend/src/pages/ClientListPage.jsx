// src/pages/ClientListPage.jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios'; // Para fazer chamadas à API
import { auth } from '../firebaseConfig'; // Para pegar o token
import {
  Box,
  Typography,
  CircularProgress,
  Alert,
  TableContainer,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Paper,
  Button,
  IconButton,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit';

// A URL da sua API backend
const API_BASE_URL = "http://localhost:8000";

function ClientListPage() {
  const [clients, setClients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchClients = async () => {
      setLoading(true);
      setError('');
      try {
        // 1. Obter o token do usuário logado
        const user = auth.currentUser;
        if (!user) {
          throw new Error("Usuário não autenticado.");
        }
        const token = await user.getIdToken();

        // 2. Fazer a chamada para a API protegida, enviando o token
        const response = await axios.get(`${API_BASE_URL}/admin/clientes`, {
          headers: {
            Authorization: `Bearer ${token}` // Envia o token no cabeçalho
          }
        });
        setClients(response.data);

      } catch (err) {
        console.error("Erro ao buscar clientes:", err);
        setError(err.response?.data?.detail || err.message || "Erro ao carregar clientes.");
      } finally {
        setLoading(false);
      }
    };

    fetchClients();
  }, []); // Executa apenas uma vez ao carregar a página

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>;
  }

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h5" gutterBottom>
        Lista de Clientes (Cabeleireiros)
      </Typography>
      <TableContainer component={Paper} elevation={3}>
        <Table sx={{ minWidth: 650 }} aria-label="simple table">
          <TableHead sx={{ backgroundColor: 'grey.200' }}>
            <TableRow>
              <TableCell sx={{ fontWeight: 'bold' }}>Nome do Salão</TableCell>
              <TableCell sx={{ fontWeight: 'bold' }}>ID (Telefone)</TableCell>
              <TableCell sx={{ fontWeight: 'bold' }}>ID Calendário</TableCell>
              <TableCell sx={{ fontWeight: 'bold' }} align="right">Ações</TableCell>
              {/* Adicionaremos mais colunas depois (Dias de trabalho, etc.) */}
            </TableRow>
          </TableHead>
          <TableBody>
            {clients.map((client) => (
              <TableRow
                key={client.id}
                sx={{ '&:last-child td, &:last-child th': { border: 0 } }}
              >
                <TableCell component="th" scope="row">
                  {client.nome_salao}
                </TableCell>
                <TableCell>{client.id}</TableCell>
                <TableCell>{client.calendar_id || 'Não definido'}</TableCell>
                <TableCell align="right">
                  <IconButton
                    component={RouterLink}
                    to={`/clientes/editar/${client.id}`} // Link dinâmico
                    color="primary"
                    aria-label="edit client"
                  >
                    <EditIcon />
                  </IconButton>
                </TableCell>
              </TableRow>

            ))}
            {clients.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} align="center">Nenhum cliente cadastrado ainda.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
      <Button
        variant="contained"
        component={RouterLink}
        to="/clientes/novo"
        startIcon={<AddIcon />}
      >
        Adicionar Cliente
      </Button>
    </Box>
  );
}

export default ClientListPage;
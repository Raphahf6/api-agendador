// src/pages/AddClientPage.jsx
import React, { useState } from 'react';
import axios from 'axios';
import { auth } from '../firebaseConfig';
import { useNavigate, Link as RouterLink } from 'react-router-dom';
import {
    Box, Button, TextField, Typography, Container, Paper, Alert,
    FormGroup, FormControlLabel, Checkbox, Grid, CircularProgress
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack'; // Ícone para voltar

const API_BASE_URL = "http://localhost:8000";

const weekDays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const weekDaysPT = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];

function AddClientPage() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState({
    nome_salao: '',
    numero_whatsapp: '+55', // Começa com o DDI Brasil
    calendar_id: '',
    dias_trabalho: ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'], // Padrão Seg-Sex
    horario_inicio: '09:00',
    horario_fim: '18:00',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prevState => ({
      ...prevState,
      [name]: value
    }));
  };

  const handleCheckboxChange = (event) => {
    const { name, checked } = event.target;
    setFormData(prevState => {
      const dias = checked
        ? [...prevState.dias_trabalho, name]
        : prevState.dias_trabalho.filter(day => day !== name);
      // Ordena os dias para consistência (opcional)
      dias.sort((a, b) => weekDays.indexOf(a) - weekDays.indexOf(b));
      return { ...prevState, dias_trabalho: dias };
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    try {
      const user = auth.currentUser;
      if (!user) throw new Error("Usuário não autenticado.");
      const token = await user.getIdToken();

      // Validação simples do telefone (pode ser melhorada)
      if (!/^\+55\d{10,11}$/.test(formData.numero_whatsapp)) {
          throw new Error("Formato do WhatsApp inválido. Use +55DDDNumero (ex: +5511999998888)");
      }

      // Envia os dados para a API
      const response = await axios.post(`${API_BASE_URL}/admin/clientes`, formData, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      });

      setSuccess(`Cliente "${response.data.nome_salao}" criado com sucesso! Redirecionando...`);
      // Limpa o formulário após sucesso (opcional)
      // setFormData({ ... }); 

      // Redireciona para a lista de clientes após um pequeno delay
      setTimeout(() => {
        navigate('/clientes'); 
      }, 2000);

    } catch (err) {
      console.error("Erro ao criar cliente:", err);
      setError(err.response?.data?.detail || err.message || "Erro ao salvar cliente.");
      setLoading(false); // Permite tentar novamente em caso de erro
    } 
    // Não setamos setLoading(false) no sucesso, pois redirecionamos
  };

  return (
    <Container component="main" maxWidth="md" sx={{ mt: 4, mb: 4 }}>
      <Button 
         component={RouterLink} 
         to="/clientes" // Link para voltar para a lista
         startIcon={<ArrowBackIcon />} 
         sx={{ mb: 2 }}
      >
        Voltar para Lista
      </Button>
      <Paper elevation={3} sx={{ padding: 4 }}>
        <Typography component="h1" variant="h5" sx={{ mb: 3 }}>
          Adicionar Novo Cliente (Cabeleireiro)
        </Typography>
        <Box component="form" onSubmit={handleSubmit} noValidate>
          <Grid container spacing={2}>
            <Grid item xs={12}>
              <TextField
                required
                fullWidth
                id="nome_salao"
                label="Nome do Salão"
                name="nome_salao"
                value={formData.nome_salao}
                onChange={handleChange}
                disabled={loading}
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                required
                fullWidth
                id="numero_whatsapp"
                label="WhatsApp (ex: +55119...)"
                name="numero_whatsapp"
                value={formData.numero_whatsapp}
                onChange={handleChange}
                disabled={loading}
                helperText="Número usado como ID único."
              />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField
                required
                fullWidth
                id="calendar_id"
                label="ID da Agenda Google Calendar"
                name="calendar_id"
                value={formData.calendar_id}
                onChange={handleChange}
                disabled={loading}
                helperText="Ex: seuemail@gmail.com ou ID@group..."
              />
            </Grid>
            <Grid item xs={12}>
               <Typography variant="subtitle1" gutterBottom>Dias de Trabalho</Typography>
               <FormGroup row>
                 {weekDays.map((day, index) => (
                   <FormControlLabel
                     key={day}
                     control={
                       <Checkbox
                         name={day}
                         checked={formData.dias_trabalho.includes(day)}
                         onChange={handleCheckboxChange}
                         disabled={loading}
                       />
                     }
                     label={weekDaysPT[index]}
                   />
                 ))}
               </FormGroup>
            </Grid>
             <Grid item xs={6}>
              <TextField
                required
                fullWidth
                id="horario_inicio"
                label="Horário Início (HH:MM)"
                name="horario_inicio"
                type="time" // Input type time para facilitar
                value={formData.horario_inicio}
                onChange={handleChange}
                InputLabelProps={{ shrink: true }}
                disabled={loading}
              />
            </Grid>
             <Grid item xs={6}>
              <TextField
                required
                fullWidth
                id="horario_fim"
                label="Horário Fim (HH:MM)"
                name="horario_fim"
                type="time"
                value={formData.horario_fim}
                onChange={handleChange}
                InputLabelProps={{ shrink: true }}
                disabled={loading}
              />
            </Grid>
          </Grid>

          {error && <Alert severity="error" sx={{ mt: 3 }}>{error}</Alert>}
          {success && <Alert severity="success" sx={{ mt: 3 }}>{success}</Alert>}

          <Button
            type="submit"
            fullWidth
            variant="contained"
            sx={{ mt: 3, mb: 2 }}
            disabled={loading}
          >
            {loading ? <CircularProgress size={24} color="inherit" /> : 'Salvar Novo Cliente'}
          </Button>
        </Box>
      </Paper>
    </Container>
  );
}

export default AddClientPage;
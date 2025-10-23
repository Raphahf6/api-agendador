// src/pages/EditClientPage.jsx (Versão FINAL CORRIGIDA - Sem Duplicatas)
import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { auth } from '../firebaseConfig';
import { useNavigate, useParams, Link as RouterLink } from 'react-router-dom';
import {
    Box, Button, TextField, Typography, Container, Paper, Alert,
    FormGroup, FormControlLabel, Checkbox, Grid, CircularProgress, IconButton, Divider,
    Stack, Chip
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline';
import DeleteIcon from '@mui/icons-material/Delete';
import PaletteIcon from '@mui/icons-material/Palette';
import BusinessIcon from '@mui/icons-material/Business';
import ScheduleIcon from '@mui/icons-material/Schedule';
import DesignServicesIcon from '@mui/icons-material/DesignServices';

const API_BASE_URL = "http://localhost:8000";
const weekDays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const weekDaysPT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];

function EditClientPage() {
    const navigate = useNavigate();
    const { clientId } = useParams();
    const [formData, setFormData] = useState({
        id: clientId, nome_salao: '', tagline: '', calendar_id: '',
        dias_trabalho: [], horario_inicio: '', horario_fim: '', servicos: [],
        url_logo: '', cor_primaria: '#6366F1', cor_secundaria: '#EC4899',
        cor_gradiente_inicio: '#A78BFA', cor_gradiente_fim: '#F472B6',
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    useEffect(() => {
        const fetchClientData = async () => {
            setError(''); setLoading(true);
            try {
                const user = auth.currentUser;
                if (!user) throw new Error("Usuário não autenticado.");
                const token = await user.getIdToken();
                const response = await axios.get(`${API_BASE_URL}/admin/clientes/${clientId}`, {
                    headers: { Authorization: `Bearer ${token}` }
                });
                setFormData({
                    id: response.data.id,
                    nome_salao: response.data.nome_salao || '',
                    tagline: response.data.tagline || '',
                    calendar_id: response.data.calendar_id || '',
                    dias_trabalho: response.data.dias_trabalho || [],
                    horario_inicio: response.data.horario_inicio || '09:00',
                    horario_fim: response.data.horario_fim || '18:00',
                    servicos: response.data.servicos || [],
                    url_logo: response.data.url_logo || '',
                    cor_primaria: response.data.cor_primaria || '#6366F1',
                    cor_secundaria: response.data.cor_secundaria || '#EC4899',
                    cor_gradiente_inicio: response.data.cor_gradiente_inicio || '#A78BFA',
                    cor_gradiente_fim: response.data.cor_gradiente_fim || '#F472B6',
                });
            } catch (err) {
                console.error("Erro ao buscar dados:", err);
                setError(err.response?.data?.detail || err.message || "Erro ao carregar dados.");
            } finally { setLoading(false); }
        };
        if (clientId) fetchClientData(); else { setError("ID inválido."); setLoading(false); }
    }, [clientId]);

    // --- FUNÇÕES DE MANIPULAÇÃO DO FORMULÁRIO (DEFINIDAS UMA VEZ AQUI) ---
     const handleChange = (e) => {
        const { name, value } = e.target;
        setFormData(prevState => ({ ...prevState, [name]: value }));
     };

     const handleCheckboxChange = (event) => {
        const { name, checked } = event.target;
        setFormData(prevState => {
          const dias = checked
            ? [...prevState.dias_trabalho, name]
            : prevState.dias_trabalho.filter(day => day !== name);
          dias.sort((a, b) => weekDays.indexOf(a) - weekDays.indexOf(b));
          return { ...prevState, dias_trabalho: dias };
        });
     };

     const handleServiceChange = (index, field, value) => {
        setFormData(prevState => {
            const newServicos = [...prevState.servicos];
            const updatedValue = (field === 'duracao_minutos' || field === 'preco') ? (value === '' ? null : Number(value)) : value;
            newServicos[index] = { ...newServicos[index], [field]: updatedValue };
            return { ...prevState, servicos: newServicos };
        });
     };

     const addService = () => {
        setFormData(prevState => ({
          ...prevState,
          servicos: [...prevState.servicos, { id: `new_${Date.now()}`, nome_servico: '', duracao_minutos: 30, preco: null, descricao: '' }]
        }));
     };

     const removeService = (index) => {
        setFormData(prevState => ({
          ...prevState,
          servicos: prevState.servicos.filter((_, i) => i !== index)
        }));
     };
      
     const handleSubmit = async (e) => {
        e.preventDefault();
        setSaving(true); setError(''); setSuccess('');
        try {
          const user = auth.currentUser;
          if (!user) throw new Error("Não autenticado.");
          const token = await user.getIdToken();
          
          const dataToSubmit = {
              ...formData,
              servicos: formData.servicos.map(s => ({
                  nome_servico: s.nome_servico,
                  duracao_minutos: s.duracao_minutos,
                  preco: s.preco,
                  descricao: s.descricao
              }))
          };

          const response = await axios.put(`${API_BASE_URL}/admin/clientes/${clientId}`, dataToSubmit, {
            headers: { Authorization: `Bearer ${token}` }
          });
          setSuccess(`Cliente "${response.data.nome_salao}" atualizado! Redirecionando...`);
          setTimeout(() => { navigate('/clientes'); }, 2000);
        } catch (err) {
          console.error("Erro ao atualizar:", err);
          setError(err.response?.data?.detail || err.message || "Erro ao salvar.");
          setSaving(false); 
        } 
     };
    // --- FIM DAS FUNÇÕES DE MANIPULAÇÃO ---


    if (loading) { /* ... (código de loading) ... */ }
    if (error && !success) { /* ... (código de erro inicial) ... */ }

    // --- RENDERIZAÇÃO ---
    return (
        <Container component="main" maxWidth="lg" sx={{ mt: 2, mb: 4 }}>
            <Button component={RouterLink} to="/clientes" startIcon={<ArrowBackIcon />} sx={{ mb: 2 }}>
                Voltar para Lista
            </Button>
            <Typography component="h1" variant="h4" sx={{ mb: 3, textAlign: 'center' }}>
                Editar Cliente: {formData.nome_salao || 'Carregando...'}
            </Typography>
            <Box component="form" onSubmit={handleSubmit} noValidate>
                {/* --- SEÇÃO DADOS DO SALÃO --- */}
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                    <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center' }}><BusinessIcon sx={{ mr: 1 }}/> Dados do Salão</Typography>
                    <Grid container spacing={2}>
                        {/* ... (Campos Nome, Tagline, Calendar ID, URL Logo) ... */}
                        <Grid item xs={12} md={6}> <TextField required fullWidth label="Nome do Salão" name="nome_salao" value={formData.nome_salao} onChange={handleChange} disabled={saving}/> </Grid>
                        <Grid item xs={12} md={6}> <TextField fullWidth label="Tagline / Descrição Curta" name="tagline" value={formData.tagline} onChange={handleChange} disabled={saving}/> </Grid>
                        <Grid item xs={12} md={6}> <TextField required fullWidth label="ID Agenda Google Calendar" name="calendar_id" value={formData.calendar_id} onChange={handleChange} disabled={saving} /> </Grid>
                        <Grid item xs={12} md={6}> <TextField fullWidth label="URL da Logo" name="url_logo" value={formData.url_logo} onChange={handleChange} disabled={saving}/> </Grid>
                    </Grid>
                </Paper>

                {/* --- SEÇÃO HORÁRIOS DE FUNCIONAMENTO --- */}
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                    <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center' }}><ScheduleIcon sx={{ mr: 1 }}/> Horários de Funcionamento</Typography>
                    <Grid container spacing={2}>
                       {/* ... (Checkboxes Dias, Inputs Horários) ... */}
                        <Grid item xs={12}><Typography variant="subtitle2" gutterBottom>Dias de Trabalho</Typography><FormGroup row sx={{ justifyContent: 'space-around' }}>{weekDays.map((day, index) => (<FormControlLabel key={day} control={<Checkbox size="small" name={day} checked={formData.dias_trabalho.includes(day)} onChange={handleCheckboxChange} disabled={saving}/>} label={weekDaysPT[index]}/>))}</FormGroup></Grid>
                        <Grid item xs={6}><TextField required fullWidth label="Horário Início" name="horario_inicio" type="time" value={formData.horario_inicio} onChange={handleChange} InputLabelProps={{ shrink: true }} disabled={saving}/> </Grid>
                        <Grid item xs={6}><TextField required fullWidth label="Horário Fim" name="horario_fim" type="time" value={formData.horario_fim} onChange={handleChange} InputLabelProps={{ shrink: true }} disabled={saving}/> </Grid>
                    </Grid>
                </Paper>

                {/* --- SEÇÃO CORES DA PÁGINA --- */}
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                     <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center' }}><PaletteIcon sx={{ mr: 1 }}/> Cores da Página</Typography>
                     <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center" justifyContent="space-around"> {/* Melhor alinhamento */}
                         {/* ... (Inputs de Cor) ... */}
                        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}><Typography variant="caption">Primária</Typography><input type="color" name="cor_primaria" value={formData.cor_primaria} onChange={handleChange} disabled={saving} style={{ width: 50, height: 50, border: 'none', padding: 0, cursor: 'pointer', borderRadius: '4px' }}/></Box>
                        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}><Typography variant="caption">Secundária</Typography><input type="color" name="cor_secundaria" value={formData.cor_secundaria} onChange={handleChange} disabled={saving} style={{ width: 50, height: 50, border: 'none', padding: 0, cursor: 'pointer', borderRadius: '4px' }}/></Box>
                        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}><Typography variant="caption">Grad. Início</Typography><input type="color" name="cor_gradiente_inicio" value={formData.cor_gradiente_inicio} onChange={handleChange} disabled={saving} style={{ width: 50, height: 50, border: 'none', padding: 0, cursor: 'pointer', borderRadius: '4px' }}/></Box>
                        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}><Typography variant="caption">Grad. Fim</Typography><input type="color" name="cor_gradiente_fim" value={formData.cor_gradiente_fim} onChange={handleChange} disabled={saving} style={{ width: 50, height: 50, border: 'none', padding: 0, cursor: 'pointer', borderRadius: '4px' }}/></Box>
                     </Stack>
                </Paper>
                
                {/* --- SEÇÃO SERVIÇOS --- */}
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                    <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center' }}><DesignServicesIcon sx={{ mr: 1 }}/> Serviços</Typography>
                    {formData.servicos.map((service, index) => (
                        <Paper key={service.id || `new_${index}`} variant="outlined" sx={{ p: 2, mb: 2 }}>
                            <Grid container spacing={2} alignItems="center">
                                {/* ... (Inputs Nome, Duração, Preço, Descrição, Botão Delete) ... */}
                                <Grid item xs={12} sm={5}> <TextField fullWidth size="small" required label="Nome do Serviço" value={service.nome_servico} onChange={(e) => handleServiceChange(index, 'nome_servico', e.target.value)} disabled={saving}/> </Grid>
                                <Grid item xs={6} sm={2}> <TextField fullWidth size="small" required label="Duração (min)" type="number" value={service.duracao_minutos} onChange={(e) => handleServiceChange(index, 'duracao_minutos', e.target.value)} disabled={saving}/> </Grid>
                                <Grid item xs={6} sm={2}> <TextField fullWidth size="small" label="Preço (R$)" type="number" InputProps={{ inputProps: { step: "0.01" } }} value={service.preco ?? ''} onChange={(e) => handleServiceChange(index, 'preco', e.target.value)} disabled={saving}/> </Grid>
                                <Grid item xs={12} sm={2}> <TextField fullWidth size="small" label="Descrição" value={service.descricao ?? ''} onChange={(e) => handleServiceChange(index, 'descricao', e.target.value)} disabled={saving}/> </Grid>
                                <Grid item xs={12} sm={1} sx={{ textAlign: 'right' }}> <IconButton size="small" onClick={() => removeService(index)} color="error" disabled={saving}><DeleteIcon /></IconButton> </Grid>
                            </Grid>
                        </Paper>
                    ))}
                    <Button startIcon={<AddCircleOutlineIcon />} onClick={addService} disabled={saving} sx={{ mt: 1 }}>
                        Adicionar Serviço
                    </Button>
                </Paper>

                {/* --- ALERTAS E BOTÃO SALVAR --- */}
                {error && <Alert severity="error" sx={{ mt: 3 }}>{error}</Alert>}
                {success && <Alert severity="success" sx={{ mt: 3 }}>{success}</Alert>}
                <Button type="submit" fullWidth variant="contained" size="large" sx={{ mt: 3, mb: 2 }} disabled={saving}>
                    {saving ? <CircularProgress size={24} color="inherit" /> : 'Salvar Alterações'}
                </Button>
            </Box>
        </Container>
    );
}

export default EditClientPage;
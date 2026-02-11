from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import List, Optional
from google.cloud.firestore import FieldFilter
from core.db import db
from core.auth import get_current_user
from core.models import Professional 

router = APIRouter(prefix="/admin/equipe", tags=["Equipe"])

# --- CRIAR ---
@router.post("", status_code=status.HTTP_201_CREATED)
def add_professional(pro: Professional, current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    # Busca o salão do usuário logado
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        # Prepara os dados (exclui ID pois o Firestore gera um novo)
        new_pro = pro.dict(exclude={'id'})
        
        # Garante que comissão seja salva (se vier nulo, salva 0)
        if new_pro.get('comissao') is None:
            new_pro['comissao'] = 0.0

        doc_ref = db.collection('cabeleireiros').document(salao_id).collection('profissionais').document()
        doc_ref.set(new_pro)
        
        return {"message": "Profissional adicionado", "id": doc_ref.id}
    except Exception as e:
        raise HTTPException(500, f"Erro ao salvar: {str(e)}")

# --- LISTAR ---
@router.get("", response_model=List[Professional])
def list_professionals(current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        pros_ref = db.collection('cabeleireiros').document(salao_id).collection('profissionais')
        docs = pros_ref.stream()
        # Retorna os dados + o ID do documento
        return [{**doc.to_dict(), "id": doc.id} for doc in docs]
    except Exception as e:
        raise HTTPException(500, "Erro ao listar equipe")

# --- ATUALIZAR (NOVA ROTA) ---
@router.put("/{pro_id}")
def update_professional(pro_id: str, pro: Professional, current_user: dict = Depends(get_current_user)):
    """Atualiza dados e comissão sem mudar o ID"""
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        pro_ref = db.collection('cabeleireiros').document(salao_id).collection('profissionais').document(pro_id)
        
        # Atualiza apenas os campos enviados
        update_data = pro.dict(exclude={'id'})
        pro_ref.update(update_data)
        
        return {"message": "Profissional atualizado com sucesso"}
    except Exception as e:
        raise HTTPException(500, f"Erro ao atualizar: {str(e)}")

# --- DELETAR ---
@router.delete("/{pro_id}")
def delete_professional(pro_id: str, current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    salao_id = docs[0].id
    
    db.collection('cabeleireiros').document(salao_id).collection('profissionais').document(pro_id).delete()
    return {"message": "Profissional removido"}
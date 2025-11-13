from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from google.cloud.firestore import FieldFilter
from core.db import db
from core.auth import get_current_user
from datetime import datetime

router = APIRouter(prefix="/admin/estoque", tags=["Estoque"])

# --- Modelos Pydantic ---
class ProductBase(BaseModel):
    nome: str
    categoria: str = "Geral" # Ex: Revenda, Consumo Interno, Tintas
    quantidade_atual: int = 0
    quantidade_minima: int = 5 # Para o alerta de estoque baixo
    preco_custo: float = 0.0
    preco_venda: float = 0.0 # Se for para revenda

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    nome: Optional[str] = None
    categoria: Optional[str] = None
    quantidade_atual: Optional[int] = None
    quantidade_minima: Optional[int] = None
    preco_custo: Optional[float] = None
    preco_venda: Optional[float] = None

class ProductResponse(ProductBase):
    id: str
    status: str # 'ok', 'low', 'critical'

# --- Helper para determinar status ---
def get_stock_status(qty, min_qty):
    if qty <= 0: return 'critical'
    if qty <= min_qty: return 'low'
    return 'ok'

# --- Rotas ---

@router.post("/produtos", status_code=status.HTTP_201_CREATED)
def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    
    # Busca ID do Salão
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        new_prod = product.dict()
        new_prod['createdAt'] = datetime.utcnow()
        
        doc_ref = db.collection('cabeleireiros').document(salao_id).collection('produtos').document()
        doc_ref.set(new_prod)
        
        return {"message": "Produto cadastrado", "id": doc_ref.id}
    except Exception as e:
        print(e)
        raise HTTPException(500, "Erro ao salvar produto")

@router.get("/produtos", response_model=List[ProductResponse])
def list_products(current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        products_ref = db.collection('cabeleireiros').document(salao_id).collection('produtos')
        docs = products_ref.stream()
        
        result = []
        for doc in docs:
            data = doc.to_dict()
            status_stock = get_stock_status(data.get('quantidade_atual', 0), data.get('quantidade_minima', 5))
            result.append({**data, "id": doc.id, "status": status_stock})
            
        # Ordena: Críticos primeiro, depois Baixos, depois OK
        status_order = {'critical': 0, 'low': 1, 'ok': 2}
        result.sort(key=lambda x: status_order[x['status']])
        
        return result
    except Exception as e:
        print(e)
        raise HTTPException(500, "Erro ao listar produtos")

@router.put("/produtos/{prod_id}")
def update_product(prod_id: str, update: ProductUpdate, current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        ref = db.collection('cabeleireiros').document(salao_id).collection('produtos').document(prod_id)
        # Exclude unset para atualizar apenas o que foi enviado
        ref.update(update.dict(exclude_unset=True))
        return {"message": "Produto atualizado"}
    except Exception as e:
        raise HTTPException(500, "Erro ao atualizar")

@router.delete("/produtos/{prod_id}")
def delete_product(prod_id: str, current_user: dict = Depends(get_current_user)):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    salao_id = docs[0].id
    
    db.collection('cabeleireiros').document(salao_id).collection('produtos').document(prod_id).delete()
    return {"message": "Produto removido"}

@router.patch("/produtos/{prod_id}/ajuste")
def quick_adjust_stock(prod_id: str, amount: int, current_user: dict = Depends(get_current_user)):
    """
    Rota rápida para +1 ou -1.
    amount pode ser positivo ou negativo.
    """
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    salao_id = docs[0].id

    ref = db.collection('cabeleireiros').document(salao_id).collection('produtos').document(prod_id)
    doc = ref.get()
    if not doc.exists: raise HTTPException(404, "Produto não encontrado")
    
    current_qty = doc.to_dict().get('quantidade_atual', 0)
    new_qty = max(0, current_qty + amount) # Não permite negativo
    
    ref.update({'quantidade_atual': new_qty})
    return {"new_quantity": new_qty}
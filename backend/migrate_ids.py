# backend/migrate_ids.py
import logging
import os
import firebase_admin
from firebase_admin import firestore, credentials, initialize_app
from dotenv import load_dotenv


load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Inicialização do Firestore (necessária para scripts standalone)
try:
    if not firebase_admin._apps:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        initialize_app(credentials.Certificate(cred_path))
    db = firestore.client()
except Exception as e:
    logging.error(f"Falha CRÍTICA ao inicializar Firebase: {e}")
    exit()

def clean_phone_id(raw_id: str) -> str:
    """Remove o '+' e espaços em branco do ID."""
    if not raw_id:
        return ""
    return raw_id.strip().replace('+', '')

def migrate_single_salon(old_id: str):
    """Copia um documento de salão e todas as suas subcoleções para um novo ID limpo."""
    new_id = clean_phone_id(old_id)
    
    if old_id == new_id:
        logging.info(f"ID {old_id} já está limpo. Pulando.")
        return

    logging.info(f"INICIANDO MIGRAÇÃO: {old_id} -> {new_id}")
    
    old_salon_ref = db.collection('cabeleireiros').document(old_id)
    new_salon_ref = db.collection('cabeleireiros').document(new_id)
    
    # 1. Copiar o Documento Principal (Cabeleireiros)
    old_doc = old_salon_ref.get()
    if not old_doc.exists:
        logging.warning(f"Documento de origem '{old_id}' não existe. Abortando.")
        return

    old_data = old_doc.to_dict()
    
    # --- Atualização de Referência Interna ---
    # É CRÍTICO atualizar o 'ownerUID' para o novo ID se o UID for o próprio telefone (embora geralmente o ownerUID seja o UID do Firebase Auth)
    # Assumindo que o ownerUID é o UID do Auth, apenas copiamos os dados.
    
    # Faz uma cópia da chave pública e a remove para re-inserção segura no novo doc
    mp_public_key = old_data.pop('mp_public_key', None) 
    
    new_salon_ref.set(old_data)
    logging.info(f"PASSOS 1-2: Documento principal copiado. ID alterado para: {new_id}")

    # 2. Copiar as Subcoleções (Iterar manualmente)
    subcollections_to_migrate = ['agendamentos', 'clientes', 'servicos', 'registros'] # Adicione outras se houver
    
    for sub_name in subcollections_to_migrate:
        old_sub_collection = old_salon_ref.collection(sub_name)
        new_sub_collection = new_salon_ref.collection(sub_name)
        
        count = 0
        for doc in old_sub_collection.stream():
            new_sub_collection.document(doc.id).set(doc.to_dict())
            
            # --- ATUALIZAÇÃO DE LINKS INTERNOS (Se a subcoleção referencia o salaoId/clienteId) ---
            if sub_name == 'agendamentos' and 'salaoId' in doc.to_dict():
                 new_sub_collection.document(doc.id).update({'salaoId': new_id})

            count += 1
            
        logging.info(f"PASSOS 3: {count} documentos migrados para subcoleção '{sub_name}'.")

    # 3. Deletar o Documento Antigo
    # OBS: Deletar a coleção principal não deleta as subcoleções (você precisa deletar subcoleções individualmente)
    
    # Para scripts de migração, é mais seguro apenas "marcar" o antigo como inativo
    old_salon_ref.update({
        'DELETED_ID_MIGRATED_TO': new_id,
        'google_sync_enabled': firestore.DELETE_FIELD # Limpa tokens antigos
    })
    
    # Opcional: Deletar o documento antigo completamente. (Cuidado!)
    # old_salon_ref.delete() 

    logging.info(f"MIGRAÇÃO CONCLUÍDA: {old_id} -> {new_id} (Verifique o Firestore).")


if __name__ == '__main__':
    # <<< LISTA DE IDs INCORRETOS QUE VOCÊ PRECISA CORRIGIR >>>
    INCORRECT_IDS = [
        # Exemplo: Se o seu salão tem ID '+5511988062634' com o '+'
        # e o erro veio com espaço: ' 5511988062634'
        '+5511988062634',
        '+5511970182202', # O ID que estava na URL de erro
        # Adicione outros IDs que precisam de correção aqui.
        '+5511988888888' # Exemplo de ID com espaço no final
    ]
    
    for id_to_fix in INCORRECT_IDS:
        migrate_single_salon(id_to_fix)
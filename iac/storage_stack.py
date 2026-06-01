import os
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Charger les variables d'environnement du fichier .env
load_dotenv()

# Configuration
PRIMARY_REGION = os.getenv("PRIMARY_REGION", "us-east-1")
BACKUP_REGION = os.getenv("BACKUP_REGION", "us-east-2")
BUCKET_BASE_NAME = os.getenv("BUCKET_BASE_NAME", "ecosens-archive")

# Noms uniques pour les buckets
PRIMARY_BUCKET_NAME = f"{BUCKET_BASE_NAME}-primary-{PRIMARY_REGION}"
BACKUP_BUCKET_NAME = f"{BUCKET_BASE_NAME}-backup-{BACKUP_REGION}"

# Initialisation des clients Boto3
s3_primary = boto3.client('s3', region_name=PRIMARY_REGION)
s3_backup = boto3.client('s3', region_name=BACKUP_REGION)
iam_client = boto3.client('iam')

def create_bucket(s3_client, bucket_name, region):
    """Crée un bucket S3 et active le versioning (requis pour la réplication)."""
    try:
        print(f"Création du bucket {bucket_name} en {region}...")
        
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        
        # Activer le versioning (Obligatoire pour la réplication CRR)
        s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        print(f"✅ Bucket {bucket_name} créé et versionné.")
    except ClientError as e:
        print(f"❌ Erreur lors de la création du bucket {bucket_name}: {e}")

def get_lab_role_arn():
    """Récupère l'ARN du rôle LabRole imposé par AWS Academy."""
    try:
        response = iam_client.get_role(RoleName='LabRole')
        role_arn = response['Role']['Arn']
        print(f"ℹ️ Récupération du LabRole AWS Academy réussie : {role_arn}")
        return role_arn
    except ClientError as e:
        print(f"❌ Impossible de récupérer le LabRole. Es-tu bien sur AWS Academy ? Erreur: {e}")
        raise e

def setup_replication(source_client, source_bucket, dest_bucket_arn, role_arn):
    """Configure la règle de réplication cross-region sur le bucket principal."""
    replication_config = {
        'Role': role_arn,
        'Rules': [
            {
                'ID': 'EcosensReplicationRule',
                'Status': 'Enabled',
                'Priority': 1,
                'Filter': {'Prefix': ''}, # Réplique absolument tout
                'Destination': {
                    'Bucket': dest_bucket_arn,
                    'StorageClass': 'STANDARD'
                },
                'DeleteMarkerReplication': {'Status': 'Disabled'}
            }
        ]
    }
    try:
        source_client.put_bucket_replication(
            Bucket=source_bucket,
            ReplicationConfiguration=replication_config
        )
        print(f"✅ Réplication configurée de {source_bucket} vers {dest_bucket_arn} en utilisant LabRole.")
    except ClientError as e:
        print(f"❌ Erreur lors de la configuration de la réplication : {e}")

def main():
    # 1. Création des deux buckets S3
    create_bucket(s3_primary, PRIMARY_BUCKET_NAME, PRIMARY_REGION)
    create_bucket(s3_backup, BACKUP_BUCKET_NAME, BACKUP_REGION)
    
    # 2. Récupération du rôle existant AWS Academy
    try:
        lab_role_arn = get_lab_role_arn()
    except Exception:
        return
    
    # 3. Mise en place de la réplication CRR
    dest_bucket_arn = f"arn:aws:s3:::{BACKUP_BUCKET_NAME}"
    setup_replication(s3_primary, PRIMARY_BUCKET_NAME, dest_bucket_arn, lab_role_arn)
    
    # 4. Génération des endpoints (URLs) pour ton app.py
    primary_endpoint = f"https://{PRIMARY_BUCKET_NAME}.s3.{PRIMARY_REGION}.amazonaws.com"
    backup_endpoint = f"https://{BACKUP_BUCKET_NAME}.s3.{BACKUP_REGION}.amazonaws.com"
    
    print("\n--- CONFIGURATION POUR APP.PY ---")
    print(f"PRIMARY_S3_ENDPOINT = '{primary_endpoint}'")
    print(f"BACKUP_S3_ENDPOINT = '{backup_endpoint}'")
    
    return {
        "primary_bucket": PRIMARY_BUCKET_NAME,
        "backup_bucket": BACKUP_BUCKET_NAME,
        "primary_endpoint": primary_endpoint,
        "backup_endpoint": backup_endpoint
    }

if __name__ == "__main__":
    endpoints = main()

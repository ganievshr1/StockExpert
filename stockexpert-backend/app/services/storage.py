from io import BytesIO
from minio import Minio
from minio.error import S3Error
from app.core.config import settings

def client():
    return Minio(settings.MINIO_ENDPOINT,access_key=settings.MINIO_ACCESS_KEY,secret_key=settings.MINIO_SECRET_KEY,secure=settings.MINIO_SECURE)
def put(key,data,content_type='application/octet-stream'):
    c=client()
    if not c.bucket_exists(settings.MINIO_BUCKET):
        try: c.make_bucket(settings.MINIO_BUCKET)
        except S3Error as e:
            if e.code not in ('BucketAlreadyOwnedByYou','BucketAlreadyExists'): raise
    c.put_object(settings.MINIO_BUCKET,key,BytesIO(data),len(data),content_type=content_type)
def get(key):
    response=client().get_object(settings.MINIO_BUCKET,key)
    try: return response.read()
    finally: response.close();response.release_conn()

def delete_key(key):
    c=client()
    if c.bucket_exists(settings.MINIO_BUCKET):c.remove_object(settings.MINIO_BUCKET,key)

def delete_session_files(organization_id,sid):
    """Exact session prefix, including source files and generated reports; idempotent."""
    from minio.deleteobjects import DeleteObject
    c=client();prefix=f'{organization_id}/{sid}/'
    if not c.bucket_exists(settings.MINIO_BUCKET):return
    objects=c.list_objects(settings.MINIO_BUCKET,prefix=prefix,recursive=True)
    errors=list(c.remove_objects(settings.MINIO_BUCKET,(DeleteObject(o.object_name) for o in objects)))
    if errors:raise RuntimeError('Could not delete all session objects; retry cleanup')


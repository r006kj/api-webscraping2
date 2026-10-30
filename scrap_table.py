import json
import uuid
from datetime import datetime
from decimal import Decimal

import boto3
import requests

API_URL = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{anio}"
NUM_SISMOS = 10
TABLE_NAME = "TablaSismosIGP"


def obtener_sismos(anio):
    response = requests.get(
        API_URL.format(anio=anio),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    # DynamoDB no acepta float -> Decimal
    return response.json(parse_float=Decimal)


def lambda_handler(event, context):
    anio = datetime.now().year

    # Obtener sismos del año actual (y del anterior si no alcanzan)
    try:
        sismos = obtener_sismos(anio)
        if len(sismos) < NUM_SISMOS:
            sismos += obtener_sismos(anio - 1)
    except requests.RequestException as e:
        return {
            'statusCode': 502,
            'body': f'Error al acceder a la API del IGP: {e}'
        }

    if not sismos:
        return {
            'statusCode': 404,
            'body': 'No se encontraron sismos'
        }

    # Ordenar del más reciente al más antiguo y tomar los últimos N
    sismos.sort(
        key=lambda s: (str(s.get('fecha_utc') or ''), str(s.get('hora_utc') or '')),
        reverse=True
    )
    ultimos = sismos[:NUM_SISMOS]

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(TABLE_NAME)

    # Eliminar todos los elementos de la tabla (con paginación)
    scan = table.scan(ProjectionExpression='id')
    items = scan['Items']
    while 'LastEvaluatedKey' in scan:
        scan = table.scan(
            ProjectionExpression='id',
            ExclusiveStartKey=scan['LastEvaluatedKey']
        )
        items += scan['Items']

    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={'id': item['id']})

    # Insertar los nuevos datos
    with table.batch_writer() as batch:
        for i, sismo in enumerate(ultimos, start=1):
            sismo['#'] = i
            sismo['id'] = str(uuid.uuid4())
            batch.put_item(Item=sismo)

    # Decimal -> float para que la respuesta sea serializable
    body = json.loads(json.dumps(ultimos, default=float))

    return {
        'statusCode': 200,
        'body': body
    }

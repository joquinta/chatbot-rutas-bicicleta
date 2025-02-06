# -*- coding: utf-8 -*-
"""Planificador_rutas_bicis.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1DEw4XYYPsb5xnfTfW6U_BYdbXKEGGTW7
"""

import streamlit as st
from datetime import datetime, timedelta
import json
import requests
import re
from langchain.adapters.openai import convert_openai_messages
from langchain_community.chat_models import ChatOpenAI
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Claves API (definidas en .env o en la configuración de Streamlit)
OWM_API_KEY = os.getenv("OWM_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Función para obtener latitud y longitud con OpenWeatherMap Geocoding API
def obtener_coordenadas(lugar):
    lugar_busqueda = f"{lugar},cl"
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={lugar_busqueda}&limit=1&appid={OWM_API_KEY}"
    respuesta = requests.get(url).json()
    
    if not respuesta:
        st.warning(f"No se encontraron coordenadas para {lugar}.")
        return None, None
    
    return respuesta[0]["lat"], respuesta[0]["lon"]

# Función para obtener la distancia y el tiempo estimado con OpenRouteService
def calcular_distancia_tiempo(puntos):
    coords = [[puntos["inicio"]["lon"], puntos["inicio"]["lat"]]]

    if "intermedios" in puntos and puntos["intermedios"]:
        for intermedio in puntos["intermedios"]:
            coords.append([intermedio["lon"], intermedio["lat"]])

    coords.append([puntos["destino"]["lon"], puntos["destino"]["lat"]])

    url = "https://api.openrouteservice.org/v2/directions/cycling-regular"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    data = {"coordinates": coords, "format": "json"}

    respuesta = requests.post(url, headers=headers, json=data).json()
    
    if "routes" not in respuesta:
        st.error("Error en la API de OpenRouteService.")
        return None, None
    
    distancia_total = respuesta["routes"][0]["summary"]["distance"] / 1000  
    tiempo_total = respuesta["routes"][0]["summary"]["duration"] / 3600  

    return distancia_total, tiempo_total

# Función para obtener el clima con OpenWeatherMap, eligiendo la hora más cercana hacia arriba
def obtener_clima(lat, lon, fecha_hora):
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric&lang=es"
    respuesta = requests.get(url).json()

    if respuesta.get("cod") != "200":
        return {"temperatura": "N/A", "condiciones": "No disponible", "viento": "N/A", "fecha_consulta": fecha_hora}

    predicciones_futuras = [p for p in respuesta["list"] if datetime.utcfromtimestamp(p["dt"]) >= fecha_hora]

    if not predicciones_futuras:
        return {"temperatura": "N/A", "condiciones": "No disponible", "viento": "N/A", "fecha_consulta": fecha_hora}

    mejor_prediccion = min(predicciones_futuras, key=lambda x: datetime.utcfromtimestamp(x["dt"]))

    viento_kmh = round(mejor_prediccion["wind"]["speed"] * 3.6, 1)

    return {
        "temperatura": int(mejor_prediccion['main']['temp']),
        "condiciones": mejor_prediccion["weather"][0]["description"].capitalize(),
        "viento": viento_kmh,
        "fecha_consulta": fecha_hora.strftime("%Y-%m-%d %H:%M")  
    }

# Función para generar recomendaciones usando el LLM
def generar_recomendacion_con_llm(climas, distancia, tiempo_estimado):
    resumen_clima = "\n".join(
        f"- {clima['nombre']} ({clima['hora_estimada'].strftime('%H:%M')}): {clima['clima']['condiciones']}, "
        f"Temperatura: {clima['clima']['temperatura']}°C, Viento: {clima['clima']['viento']} km/h"
        for clima in climas
    )

    prompt = [
        {"role": "system", "content": "Eres un experto en ciclismo de nivel intermedio/avanzado. Genera una recomendación técnica y detallada para ciclistas experimentados basada en los siguientes datos:"},
        {"role": "user", "content": f"Datos de la ruta:\n"
                                    f"- Distancia total: {distancia:.2f} km\n"
                                    f"- Tiempo estimado: {tiempo_estimado:.2f} horas\n"
                                    f"Datos del clima en los puntos de la ruta:\n"
                                    f"{resumen_clima}\n\n"
                                    f"Por favor, genera una recomendación técnica y útil para ciclistas de nivel intermedio/avanzado, teniendo en cuenta las condiciones climáticas y la duración de la ruta."}
    ]

    lc_messages = convert_openai_messages(prompt)
    response = ChatOpenAI(model='gpt-4', openai_api_key=OPENAI_API_KEY).invoke(lc_messages).content

    return response

# Interfaz de Streamlit
st.title("Planificador de Rutas de Bicicleta en Chile 🚴‍♂️")

query = st.text_input("Ingresa tu ruta:", placeholder="Ej: Saldré a pedalear el 8 de febrero a las 8:00 desde Osorno, pasando por San Pablo y La Unión, hasta Valdivia.", key="input")

if query:
    extract_prompt = [
        {"role": "system", "content": "Extrae los siguientes datos en **JSON puro**, sin explicaciones:\n"
         "{\n"
         "  \"hora_salida\": \"YYYY-MM-DD HH:MM\",\n"
         "  \"lugares\": {\n"
         "    \"inicio\": \"Nombre del lugar de inicio\",\n"
         "    \"intermedios\": [\"Nombre del punto intermedio opcional 1\", \"Nombre del punto intermedio opcional 2\"],\n"
         "    \"destino\": \"Nombre del destino final\"\n"
         "  }\n"
         "}"
        },
        {"role": "user", "content": query}
    ]

    lc_messages = convert_openai_messages(extract_prompt)
    response = ChatOpenAI(model='gpt-4', openai_api_key=OPENAI_API_KEY).invoke(lc_messages).content

    match = re.search(r"\{.*\}", response, re.DOTALL)
    if match:
        try:
            extracted_data = json.loads(match.group(0))
        except json.JSONDecodeError:
            st.error("Error al decodificar JSON.")
            extracted_data = None
    else:
        st.error("No se encontró JSON en la respuesta del modelo.")
        extracted_data = None

    if extracted_data:
        # Forzar el año 2025
        extracted_data["hora_salida"] = re.sub(r"^\d{4}", "2025", extracted_data["hora_salida"])

        hora_salida = datetime.strptime(extracted_data["hora_salida"], "%Y-%m-%d %H:%M")
        
        climas = []
        clima_inicio = obtener_clima(0, 0, hora_salida)  # Solo para prueba
        climas.append({"nombre": "Ejemplo", "clima": clima_inicio, "hora_estimada": hora_salida})

        st.success("### Resumen de la ruta:")
        st.write(f"📅 **Fecha de consulta de clima:** {clima_inicio['fecha_consulta']}")
        st.write("---")
        
        for clima in climas:
            st.write(
                f"📍 **{clima['nombre']}** ({clima['hora_estimada'].strftime('%H:%M')}): "
                f"{clima['clima']['condiciones']}, Temperatura: {clima['clima']['temperatura']}°C, "
                f"Viento: {clima['clima']['viento']} km/h"
            )




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
    # Forzar la búsqueda en Chile agregando ",cl" al final del lugar
    lugar_busqueda = f"{lugar},cl"
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={lugar_busqueda}&limit=1&appid={OWM_API_KEY}"
    respuesta = requests.get(url).json()

    if not respuesta:
        st.warning(f"No se encontraron coordenadas para {lugar}.")
        return None, None

    return respuesta[0]["lat"], respuesta[0]["lon"]

# Función para obtener la distancia, el tiempo estimado y el desnivel positivo acumulado con OpenRouteService
def calcular_distancia_tiempo(puntos):
    coords = [[puntos["inicio"]["lon"], puntos["inicio"]["lat"]]]

    if "intermedios" in puntos and puntos["intermedios"]:
        for intermedio in puntos["intermedios"]:
            coords.append([intermedio["lon"], intermedio["lat"]])

    coords.append([puntos["destino"]["lon"], puntos["destino"]["lat"]])

    url = "https://api.openrouteservice.org/v2/directions/cycling-regular"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    data = {"coordinates": coords, "format": "json", "elevation": True}  # Añadido "elevation": True

    respuesta = requests.post(url, headers=headers, json=data).json()

    if "routes" not in respuesta:
        st.error("Error en la API de OpenRouteService.")
        return None, None, None

    distancia_total = respuesta["routes"][0]["summary"]["distance"] / 1000  # Convertir a km
    tiempo_total = respuesta["routes"][0]["summary"]["duration"] / 3600  # Convertir a horas
    desnivel_positivo = respuesta["routes"][0]["summary"]["ascent"] #Desnivel positivo acumulado

    return distancia_total, tiempo_total, desnivel_positivo

# Función para obtener el clima con OpenWeatherMap, eligiendo la hora más cercana hacia arriba
def obtener_clima(lat, lon, fecha_hora):
    # Forzar el año 2025
    fecha_hora = fecha_hora.replace(year=2025)

    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric&lang=es"
    respuesta = requests.get(url).json()

    if respuesta.get("cod") != "200":
        return {"temperatura": "N/A", "condiciones": "No disponible", "viento": "N/A"}, fecha_hora

    # Filtrar solo pronósticos con timestamps en el futuro (hacia arriba)
    predicciones_futuras = [p for p in respuesta["list"] if datetime.utcfromtimestamp(p["dt"]) >= fecha_hora]

    if not predicciones_futuras:
        return {"temperatura": "N/A", "condiciones": "No disponible", "viento": "N/A"}, fecha_hora

    mejor_prediccion = min(predicciones_futuras, key=lambda x: datetime.utcfromtimestamp(x["dt"]))

    # Convertir velocidad del viento de m/s a km/h (1 m/s = 3.6 km/h)
    viento_kmh = round(mejor_prediccion["wind"]["speed"] * 3.6, 1)

    return {
        "temperatura": int(mejor_prediccion['main']['temp']),  # Temperatura sin decimales
        "condiciones": mejor_prediccion["weather"][0]["description"].capitalize(),
        "viento": viento_kmh  # Velocidad del viento en km/h
    }, fecha_hora

# Función para generar recomendaciones usando el LLM
def generar_recomendacion_con_llm(climas):
    # Crear un resumen de los datos de clima
    resumen_clima = "\n".join(
        f"- {clima['nombre']} ({clima['hora_estimada'].strftime('%H:%M')}): {clima['clima']['condiciones']}, "
        f"Temperatura: {clima['clima']['temperatura']}°C, Viento: {clima['clima']['viento']} km/h"
        for clima in climas
    )

    # Crear el prompt para el LLM
    prompt = [
        {"role": "system", "content": "Eres un experto en ciclismo de nivel avanzado. Genera una recomendación breve de la ropa requerida según el clima (tricota, chaqueta, calza larga o corta, manguillas y pierneras) y accesorios como multi-herramietasm o camara de repuesto, verificar carga de elementos electronicos. Ademas, si la salida es larga recomendar una cantidad de geles y carbohidratos por hora."},
        {"role": "user", "content": f"Datos del clima en los puntos de la ruta:\n"
                                    f"{resumen_clima}\n\n"
                                    f"Por favor, genera una recomendación breve y experta enfocada en la ropa, alimentación y accesorios más adecuada para las condiciones climáticas del viaje. Usa un formato de checklist. No entregues notas extras. NO recomiendes bidon de agua. No recomiendes bateria externa para cargar."}
    ]

    # Convertir el prompt y obtener la respuesta del LLM
    lc_messages = convert_openai_messages(prompt)
    response = ChatOpenAI(model='gpt-4o-mini', openai_api_key=OPENAI_API_KEY).invoke(lc_messages).content

    return response

# Interfaz de Streamlit
st.title("Planificador de Rutas de Bicicleta en Chile 🚴‍♂️")

# Campo de entrada sin mensaje precargado
query = st.text_input("Ingresa tu ruta (Pronóstico máximo a 5 días):", placeholder="Ej: Saldré a pedalear el 8 de febrero del 2025 a las 8:00 desde providencia a farellones, volviendo a providencia", key="input")

# Función para resetear el estado de la sesión
def reset_session_state():
    st.session_state['extracted_data'] = None
    st.session_state['hora_salida'] = None
    st.session_state['puntos'] = {"inicio": {}, "destino": {}, "intermedios": []}
    st.session_state['distancia'] = None
    st.session_state['tiempo_estimado'] = None
    st.session_state['desnivel_positivo'] = None
    st.session_state['climas'] = []

# Planificar la ruta automáticamente al presionar Enter
if query:
    # Resetear el estado de la sesión al ingresar una nueva consulta
    reset_session_state()

    # Función para extraer datos con el LLM
    def extraer_datos(query):
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
                return extracted_data
            except json.JSONDecodeError:
                st.error("Error al decodificar JSON. Respuesta del modelo: " + response)
                return None
        else:
            st.error("No se encontró JSON en la respuesta del modelo.")
            return None

    st.session_state['extracted_data'] = extraer_datos(query)

    if not st.session_state['extracted_data']:
        st.info("Por favor, proporciona más detalles sobre tu ruta (fecha, hora, puntos de inicio/fin).")
        st.stop() # Detiene la ejecución si no hay datos extraídos

    # Verificar si la hora de salida está presente
    if 'hora_salida' not in st.session_state['extracted_data'] or not st.session_state['extracted_data']['hora_salida']:
        st.info("Por favor, especifica la hora de salida en tu ruta.")
        st.stop()
    else:
        # Intentar convertir la hora de salida
        try:
            st.session_state['hora_salida'] = datetime.strptime(st.session_state['extracted_data']["hora_salida"], "%Y-%m-%d %H:%M")
        except ValueError:
            st.info("Por favor, especifica en el mensaje la hora de salida en tu ruta.")
            st.stop()

    # Obtener coordenadas de los puntos
    if not st.session_state['puntos']['inicio'].get('nombre'):
        if 'inicio' not in st.session_state['extracted_data']['lugares']:
             st.info("Por favor, especifica el punto de inicio de tu ruta.")
             st.stop()

        st.session_state['puntos']['inicio']['nombre'] = st.session_state['extracted_data']['lugares']['inicio']
        st.session_state['puntos']['inicio']['lat'], st.session_state['puntos']['inicio']['lon'] = obtener_coordenadas(st.session_state['puntos']['inicio']['nombre'])

    if not st.session_state['puntos']['destino'].get('nombre'):
        if 'destino' not in st.session_state['extracted_data']['lugares']:
             st.info("Por favor, especifica el destino de tu ruta.")
             st.stop()

        st.session_state['puntos']['destino']['nombre'] = st.session_state['extracted_data']['lugares']['destino']
        st.session_state['puntos']['destino']['lat'], st.session_state['puntos']['destino']['lon'] = obtener_coordenadas(st.session_state['puntos']['destino']['nombre'])

    if "intermedios" in st.session_state['extracted_data']['lugares'] and st.session_state['extracted_data']['lugares']['intermedios']:
        for intermedio in st.session_state['extracted_data']['lugares']['intermedios']:
            if not any(p['nombre'] == intermedio for p in st.session_state['puntos']['intermedios']):
                lat, lon = obtener_coordenadas(intermedio)
                if lat and lon:
                    st.session_state['puntos']['intermedios'].append({"nombre": intermedio, "lat": lat, "lon": lon})

    # Calcular distancia y tiempo
    if not st.session_state['distancia'] or not st.session_state['tiempo_estimado'] or not st.session_state['desnivel_positivo']:
        st.session_state['distancia'], st.session_state['tiempo_estimado'], st.session_state['desnivel_positivo'] = calcular_distancia_tiempo(st.session_state['puntos'])

    # Aplicar ajuste manual al desnivel positivo
    desnivel_ajustado = st.session_state['desnivel_positivo'] / 2
    rango_minimo = round(desnivel_ajustado - 300, 2)
    rango_maximo = round(desnivel_ajustado + 100, 2)

    # Obtener clima en los puntos clave
    # Forzar año 2025
    st.session_state['hora_salida'] = st.session_state['hora_salida'].replace(year=2025)

    st.session_state['climas'] = []

    # Clima en el inicio
    clima_inicio, fecha_inicio_api = obtener_clima(st.session_state['puntos']['inicio']['lat'], st.session_state['puntos']['inicio']['lon'], st.session_state['hora_salida'])
    st.session_state['climas'].append({"nombre": st.session_state['puntos']['inicio']['nombre'], "clima": clima_inicio, "hora_estimada": st.session_state['hora_salida']})

    # Clima en los puntos intermedios
    for i, punto in enumerate(st.session_state['puntos']['intermedios']):
        tiempo_parcial = (i + 1) * (st.session_state['tiempo_estimado'] / (len(st.session_state['puntos']['intermedios']) + 1))
        hora_estimada = st.session_state['hora_salida'] + timedelta(hours=tiempo_parcial)

        # Forzar año 2025
        hora_estimada = hora_estimada.replace(year=2025)

        clima_intermedio, fecha_intermedio_api = obtener_clima(punto['lat'], punto['lon'], hora_estimada)
        st.session_state['climas'].append({"nombre": punto['nombre'], "clima": clima_intermedio, "hora_estimada": hora_estimada})

    # Clima en el destino
    hora_destino = st.session_state['hora_salida'] + timedelta(hours=st.session_state['tiempo_estimado'])

    # Forzar año 2025
    hora_destino = hora_destino.replace(year=2025)

    clima_destino, fecha_destino_api = obtener_clima(st.session_state['puntos']['destino']['lat'], st.session_state['puntos']['destino']['lon'], hora_destino)
    st.session_state['climas'].append({"nombre": st.session_state['puntos']['destino']['nombre'], "clima": clima_destino, "hora_estimada": hora_destino})

    # Mostrar resultados de manera orgánica
    st.success("### Resumen de la ruta:")
    st.write(f"Fecha de consulta a la API OpenWeather: {fecha_inicio_api.strftime('%Y-%m-%d')}")  # Mostrar la fecha usada
    st.write(f"🚴‍♂️ **Distancia total:** {st.session_state['distancia']:.2f} km")
    st.write(f"⏳ **Tiempo estimado:** {st.session_state['tiempo_estimado']:.2f} horas")
    st.write(f"📈 **Rango desnivel estimado:** {rango_minimo} - {rango_maximo} metros")
    st.write("---")

    st.write("### Clima en los puntos de la ruta:")
    for clima in st.session_state['climas']:
        st.write(
            f"📍 **{clima['nombre']}** ({clima['hora_estimada'].strftime('%H:%M')}): "
            f"{clima['clima']['condiciones']}, Temperatura: {clima['clima']['temperatura']}°C, "
            f"Viento: {clima['clima']['viento']} km/h"
        )
    st.write("---")

    # Generar y mostrar recomendación final usando el LLM
    recomendacion = generar_recomendacion_con_llm(st.session_state['climas'])
    st.write("### Recomendación Técnica (Ropa y Alimentación):")
    st.info(recomendacion)


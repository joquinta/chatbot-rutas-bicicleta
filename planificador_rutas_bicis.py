# -*- coding: utf-8 -*-
"""Planificador_rutas_bicis.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1DEw4XYYPsb5xnfTfW6U_BYdbXKEGGTW7
"""

import streamlit as st
import json
import requests
from datetime import datetime, timedelta
from fpdf import FPDF
from langchain.adapters.openai import convert_openai_messages
from langchain_community.chat_models import ChatOpenAI

# Configurar claves API desde Streamlit Secrets
OWM_API_KEY = st.secrets["OWM_API_KEY"]
ORS_API_KEY = st.secrets["ORS_API_KEY"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# Función para obtener coordenadas
def obtener_coordenadas(lugar):
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={lugar}&limit=1&appid={OWM_API_KEY}"
    respuesta = requests.get(url).json()
    return (respuesta[0]["lat"], respuesta[0]["lon"]) if respuesta else (None, None)

# Función para obtener la distancia y el tiempo estimado
def calcular_distancia_tiempo(puntos):
    coords = [[puntos["inicio"]["lon"], puntos["inicio"]["lat"]]]
    for intermedio in puntos["intermedios"]:
        coords.append([intermedio["lon"], intermedio["lat"]])
    coords.append([puntos["destino"]["lon"], puntos["destino"]["lat"]])

    url = "https://api.openrouteservice.org/v2/directions/cycling-regular"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    data = {"coordinates": coords, "format": "json"}
    respuesta = requests.post(url, headers=headers, json=data).json()

    if "routes" not in respuesta:
        return None, None

    return respuesta["routes"][0]["summary"]["distance"] / 1000, respuesta["routes"][0]["summary"]["duration"] / 3600

# Función para obtener el clima
def obtener_clima(lat, lon, fecha_hora):
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric&lang=es"
    respuesta = requests.get(url).json()
    if respuesta.get("cod") != "200":
        return "N/A", "No disponible"
    predicciones_futuras = [p for p in respuesta["list"] if datetime.utcfromtimestamp(p["dt"]) >= fecha_hora]
    mejor_prediccion = min(predicciones_futuras, key=lambda x: datetime.utcfromtimestamp(x["dt"]))
    return f"{mejor_prediccion['main']['temp']}°C", mejor_prediccion["weather"][0]["description"].capitalize()

# Configurar la app de Streamlit
st.title("🚴‍♂️ Planificador de rutas en bicicleta")
st.caption("Ingresa tu ruta y obtén distancia, tiempo y clima.")
query = st.chat_input("Describe tu ruta de ciclismo...")

if query:
    with st.spinner("Procesando tu ruta..."):
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
        response = ChatOpenAI(model='gpt-4o-mini', openai_api_key=OPENAI_API_KEY).invoke(lc_messages).content
        extracted_data = json.loads(response)

        puntos = {"inicio": {}, "destino": {}, "intermedios": []}
        puntos["inicio"]["nombre"] = extracted_data["lugares"]["inicio"]
        puntos["inicio"]["lat"], puntos["inicio"]["lon"] = obtener_coordenadas(puntos["inicio"]["nombre"])
        puntos["destino"]["nombre"] = extracted_data["lugares"]["destino"]
        puntos["destino"]["lat"], puntos["destino"]["lon"] = obtener_coordenadas(puntos["destino"]["nombre"])

        for intermedio in extracted_data["lugares"].get("intermedios", []):
            lat, lon = obtener_coordenadas(intermedio)
            if lat and lon:
                puntos["intermedios"].append({"nombre": intermedio, "lat": lat, "lon": lon})

        distancia, tiempo_estimado = calcular_distancia_tiempo(puntos)
        hora_salida = datetime.strptime(extracted_data["hora_salida"], "%Y-%m-%d %H:%M")
        clima_inicio = obtener_clima(puntos["inicio"]["lat"], puntos["inicio"]["lon"], hora_salida)
        clima_destino = obtener_clima(puntos["destino"]["lat"], puntos["destino"]["lon"], hora_salida + timedelta(hours=tiempo_estimado))

climas_intermedios = []
for i, punto in enumerate(puntos["intermedios"]):
    tiempo_parcial = (i + 1) * (tiempo_estimado / (len(puntos["intermedios"]) + 1))
    clima_intermedio = obtener_clima(punto["lat"], punto["lon"], hora_salida + timedelta(hours=tiempo_parcial))
    climas_intermedios.append({"nombre": punto["nombre"], "clima": clima_intermedio})

st.write(f"**Distancia:** {distancia:.2f} km")
st.write(f"**Tiempo estimado:** {tiempo_estimado:.2f} horas")
st.write(f"🌤️ **Clima en {puntos['inicio']['nombre']}:** {clima_inicio}")
st.write(f"🌤️ **Clima en {puntos['destino']['nombre']}:** {clima_destino}")

for clima in climas_intermedios:
    st.write(f"🌤️ Clima en {clima['nombre']}: {clima['clima']}")

# Generar PDF después de listar la información
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.cell(200, 10, "Resumen de Ruta", ln=True, align='C')
pdf.cell(200, 10, f"Distancia: {distancia:.2f} km", ln=True)
pdf.cell(200, 10, f"Tiempo estimado: {tiempo_estimado:.2f} horas", ln=True)
pdf.cell(200, 10, f"Clima en {puntos['inicio']['nombre']}: {clima_inicio}", ln=True)
pdf.cell(200, 10, f"Clima en {puntos['destino']['nombre']}: {clima_destino}", ln=True)

pdf_filename = "resumen_ruta.pdf"
pdf.output(pdf_filename)
with open(pdf_filename, "rb") as file:
    st.download_button("Descargar resumen en PDF", file, file_name="resumen_ruta.pdf", mime="application/pdf")

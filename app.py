import dash
from dash import html

app = dash.Dash(__name__, title="Argo Float Monitor")
server = app.server  # for gunicorn

app.layout = html.Div([
    html.H1("🌊 Argo Float Monitor"),
    html.P("Dash version coming soon. Currently building tab-by-tab from "
           "the Streamlit reference implementation."),
])

if __name__ == "__main__":
    app.run(debug=True, port=8050)
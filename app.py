import streamlit as st
import osmnx as ox
import networkx as nx
import folium
from streamlit_folium import st_folium

# Cấu hình trang web
st.set_page_config(page_title="Tìm đường tránh kẹt xe", layout="wide")


# 1. HÀM XỬ LÝ DỮ LIỆU

@st.cache_data
def load_graph_and_center(place_name):
    """Tải bản đồ và tìm toạ độ trung tâm"""
    try:
        lat, lng = ox.geocode(place_name)
        center_coords = [lat, lng]
    except Exception:
        return None, None, None

    G = ox.graph_from_place(place_name, network_type='drive')
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    try:
        G = ox.truncate.largest_component(G, strongly=True)
    except AttributeError:
        G = ox.utils_graph.get_largest_component(G, strongly=True)

    return G, center_coords


@st.cache_data
def load_hospitals(place_name, _G):
    """Tải danh sách bệnh viện"""
    tags = {'amenity': 'hospital'}
    try:
        hospitals = ox.features_from_place(place_name, tags=tags)
        hospital_nodes = []
        for idx, row in hospitals.iterrows():
            centroid = row.geometry.centroid
            try:
                nearest_node = ox.distance.nearest_nodes(_G, centroid.x, centroid.y)
                hospital_nodes.append({
                    "name": row.get("name", "Bệnh viện (Không tên)"),
                    "node_id": nearest_node,
                    "coords": (centroid.y, centroid.x)
                })
            except:
                continue
        return hospital_nodes
    except:
        return []


# --- 2. KHỞI TẠO TRẠNG THÁI ---

if 'G_original' not in st.session_state:
    st.session_state.G_original = None
if 'G_active' not in st.session_state:
    st.session_state.G_active = None
if 'obstacles' not in st.session_state:
    st.session_state.obstacles = []
if 'ambulance_pos' not in st.session_state:
    st.session_state.ambulance_pos = None
if 'map_center' not in st.session_state:
    st.session_state.map_center = [10.7769, 106.7009]
if 'hospitals' not in st.session_state:
    st.session_state.hospitals = []

# --- 3. GIAO DIỆN SIDEBAR ---

with st.sidebar:
    st.title("Điều khiển")
    place_input = st.text_input("Nhập địa chỉ:", "Tan Phu District, Ho Chi Minh City, Vietnam")

    if st.button("1. Tải bản đồ mới", type="primary"):
        with st.spinner(f"Đang tải bản đồ {place_input}..."):
            try:
                G, center = load_graph_and_center(place_input)
                if G is None:
                    st.error("Không tìm thấy địa điểm!")
                else:
                    st.session_state.G_original = G
                    st.session_state.G_active = G.copy()
                    st.session_state.map_center = center
                    st.session_state.hospitals = load_hospitals(place_input, G)
                    st.session_state.obstacles = []
                    st.session_state.ambulance_pos = None
                    st.success(f"Đã chuyển đến: {place_input}")
            except Exception as e:
                st.error(f"Lỗi: {e}")

    st.divider()
    mode = st.radio("Chế độ:", ["📍 Chọn vị trí xe cứu thương", "⛔ Tạo điểm kẹt xe "])
    st.divider()
    if st.button("Reset bản đồ"):
        if st.session_state.G_original:
            st.session_state.G_active = st.session_state.G_original.copy()
            st.session_state.obstacles = []
            st.rerun()

# --- 4. XỬ LÝ CHÍNH ---

if st.session_state.G_active is not None:

    col1, col2 = st.columns([7, 3])

    with col1:
        st.subheader("Bản đồ giao thông")
        m = folium.Map(location=st.session_state.map_center, zoom_start=14)

        for hosp in st.session_state.hospitals:
            folium.Marker(location=hosp['coords'], popup=hosp['name'],
                          icon=folium.Icon(color='blue', icon='plus', prefix='fa')).add_to(m)

        for obs in st.session_state.obstacles:
            folium.CircleMarker(location=obs, radius=10, color='black', fill=True, fill_color='red',
                                popup="KẸT XE").add_to(m)

        if st.session_state.ambulance_pos:
            folium.Marker(location=st.session_state.ambulance_pos, popup="Xe cứu thương",
                          icon=folium.Icon(color='green', icon='ambulance', prefix='fa')).add_to(m)

        output = st_folium(m, width=None, height=600)

        if output['last_clicked']:
            lat, lng = output['last_clicked']['lat'], output['last_clicked']['lng']
            click_coords = (lat, lng)

            if mode == "⛔ Tạo điểm kẹt xe (Chặn đường)":
                st.session_state.obstacles.append(click_coords)
                nearest_node = ox.distance.nearest_nodes(st.session_state.G_active, lng, lat)
                try:
                    st.session_state.G_active.remove_node(nearest_node)
                except:
                    pass
                st.rerun()
            elif mode == "📍 Chọn vị trí xe cứu thương":
                st.session_state.ambulance_pos = click_coords
                st.rerun()

    #  CỘT KẾT QUẢ
    with col2:
        st.subheader("Chỉ dẫn")

        if st.session_state.ambulance_pos:
            start_coords = st.session_state.ambulance_pos
            try:
                start_node = ox.distance.nearest_nodes(st.session_state.G_active, start_coords[1], start_coords[0])
            except:
                st.error("Xe đang ở vùng kẹt xe!")
                st.stop()

            best_route = None
            best_time = float('inf')
            best_hospital = None
            best_dist = 0

            with st.spinner("Đang tính toán A*..."):
                for hosp in st.session_state.hospitals:
                    try:
                        if hosp['node_id'] not in st.session_state.G_active: continue
                        length = nx.shortest_path_length(st.session_state.G_active, start_node, hosp["node_id"],
                                                         weight='travel_time')
                        if length < best_time:
                            best_time = length
                            best_hospital = hosp
                            best_route = nx.astar_path(st.session_state.G_active, start_node, hosp["node_id"],
                                                       weight='travel_time')
                            best_dist = nx.shortest_path_length(st.session_state.G_active, start_node, hosp["node_id"],
                                                                weight='length')
                    except nx.NetworkXNoPath:
                        continue

            if best_hospital:
                st.success("Tìm thấy đường!")
                st.info(f"Đến: **{best_hospital['name']}**")
                st.warning(f"Thời gian: **{best_time / 60:.1f} phút**")
                st.write(f"Quãng đường: **{best_dist / 1000:.2f} km**")

                st.write("**Lộ trình chi tiết:**")

                m_mini = folium.Map(location=start_coords, zoom_start=14)

                route_coords = []
                for node in best_route:
                    point = st.session_state.G_active.nodes[node]
                    route_coords.append((point['y'], point['x']))


                folium.PolyLine(route_coords, color="red", weight=6, opacity=0.8).add_to(m_mini)
                folium.Marker(start_coords, icon=folium.Icon(color='green', icon='ambulance', prefix='fa'),
                              popup="Bắt đầu").add_to(m_mini)
                folium.Marker(best_hospital['coords'], icon=folium.Icon(color='red', icon='h-square', prefix='fa'),
                              popup="Đích").add_to(m_mini)

                for obs in st.session_state.obstacles:
                    folium.CircleMarker(location=obs, radius=5, color='black', fill=True, fill_color='red').add_to(
                        m_mini)

                sw = min(route_coords, key=lambda x: x[0])[0], min(route_coords, key=lambda x: x[1])[1]
                ne = max(route_coords, key=lambda x: x[0])[0], max(route_coords, key=lambda x: x[1])[1]
                m_mini.fit_bounds([sw, ne])
                st_folium(m_mini, width=None, height=500, key="minimap")
            else:
                st.error("Không tìm thấy đường đi nào!")
else:
    st.info(" Nhập tên thành phố/quận và ấn nút Tải bản đồ.")

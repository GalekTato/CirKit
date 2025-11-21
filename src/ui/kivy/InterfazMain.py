# -*- coding: utf-8 -*-
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false
import os, sys, json, math, pathlib, time, heapq, base64, tempfile
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional, Any

# -------------------------------------------------
#  RUTAS DE PROYECTO
# -------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# -------------------------------------------------
#  KIVY
# -------------------------------------------------
import kivy
kivy.require("2.3.0")

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, DictProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.button import Button
from kivy.core.window import Window
from kivy.resources import resource_add_path
from kivy.graphics import (
    Color, Line, Rectangle, Ellipse, Triangle,
    PushMatrix, PopMatrix, Rotate, Translate, InstructionGroup
)

# -------------------------------------------------
#  DIALOGO NATIVO (tk)
# -------------------------------------------------
import tkinter as tk
from tkinter import filedialog

# -------------------------------------------------
#  BACKEND
# -------------------------------------------------
from src.app.simulate import simulate
from src.app.export_pdf import export_solution_pdf
from src.domain.netlist import Netlist
from src.domain.components.resistor import Resistor
from src.domain.components.vsource import VSource
from src.domain.components.diode import IdealDiode

# -------------------------------------------------
#  CONSTANTES UI
# -------------------------------------------------
GRID = 20
PIN_R = 9

DESC = {
    "R": "Resistor ideal. Relación: V = I × R.\nOpone resistencia al flujo de corriente.",
    "V": "Fuente de voltaje ideal.\nMantiene diferencia de potencial constante.",
    "D": "Diodo ideal.\nConduce corriente del ánodo (A) al cátodo (K).",
    "J": "Nodo de unión (•).\nPunto común para conectar múltiples cables.",
}

def snap(p):  # ajusta al grid
    return (round(p[0] / GRID) * GRID, round(p[1] / GRID) * GRID)


# -------------------------------------------------
#  UNIÓN–FIND (para conectividad)
# -------------------------------------------------
class UF:
    def __init__(self):
        self.p, self.r = {}, {}

    def find(self, x):
        if x not in self.p:
            self.p[x] = x
            self.r[x] = 0
        if self.p[x] != x:
            self.p[x] = self.find(self.p[x])
        return self.p[x]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.r[ra] < self.r[rb]:
            self.p[ra] = rb
        elif self.r[ra] > self.r[rb]:
            self.p[rb] = ra
        else:
            self.p[rb] = ra
            self.r[ra] += 1


# -------------------------------------------------
#  WIDGETS DE COMPONENTES
# -------------------------------------------------
class CompWidget(Widget):
    cid = StringProperty("")
    ctype = StringProperty("")  # R, V, D
    rot = NumericProperty(0)
    selected = BooleanProperty(False)
    props = DictProperty({})

    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint = (None, None)
        self.size = (86, 36)
        self._drag = False
        self._last_tap = 0.0
        self.bind(pos=self._redraw, size=self._redraw,
                  rot=self._redraw, selected=self._redraw)
        Clock.schedule_once(lambda *_: self._redraw(), 0)

    def collide_point(self, x, y):
        cx, cy = self.center
        ang = math.radians(-(self.rot % 360))
        dx, dy = x - cx, y - cy
        c, s = math.cos(ang), math.sin(ang)
        lx, ly = dx * c - dy * s, dx * s + dy * c
        return (-48 <= lx <= 48) and (-14 <= ly <= 14)

    def pin_world(self) -> Dict[str, Tuple[float, float]]:
        out = {}
        cx, cy = self.center
        ang = math.radians(self.rot % 360)
        c, s = math.cos(ang), math.sin(ang)
        pins = {
            "R": {"A": (-44, 0), "B": (44, 0)},
            "V": {"+": (0, 22), "-": (0, -22)},
            "D": {"A": (-44, 0), "K": (44, 0)},
        }[self.ctype]
        for n, (lx, ly) in pins.items():
            x = lx * c - ly * s
            y = lx * s + ly * c
            out[n] = (cx + x, cy + y)
        return out

    def on_touch_down(self, touch):
        parent = self.parent
        if parent and getattr(parent, "mode", "") == "wire":
            return False
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        now = time.time()
        # doble clic → propiedades
        if now - self._last_tap < 0.35 and hasattr(parent, "open_properties"):
            parent.select_component(self)
            Clock.schedule_once(lambda dt: parent.open_properties(self), 0.05)
            self._last_tap = 0
            return True
        self._last_tap = now

        if hasattr(parent, "select_component"):
            parent.select_component(self)
        self._drag = True
        self._dx = touch.x - self.x
        self._dy = touch.y - self.y
        App.get_running_app().set_status(
            f"Seleccionado: {self.cid}. Doble clic para propiedades."
        )
        return True

    def on_touch_move(self, touch):
        if not self._drag:
            return super().on_touch_move(touch)
        nx = round((touch.x - self._dx) / GRID) * GRID
        ny = round((touch.y - self._dy) / GRID) * GRID
        self.pos = (nx, ny)
        if hasattr(self.parent, "redraw_wires"):
            self.parent.redraw_wires()
        return True

    def on_touch_up(self, touch):
        if not self._drag:
            return super().on_touch_up(touch)
        self._drag = False
        if hasattr(self.parent, "redraw_wires"):
            self.parent.redraw_wires()
        return True

    def _redraw(self, *_):
        self.canvas.clear()
        cx, cy = self.center
        with self.canvas:
            if self.selected:
                Color(1, 1, 1, 0.08)
                Rectangle(pos=(self.x - 4, self.y - 4),
                          size=(self.width + 8, self.height + 8))

            PushMatrix()
            Translate(cx, cy, 0)
            Rotate(angle=self.rot, axis=(0, 0, 1))

            # patitas
            Color(0.90, 0.94, 1, 1)
            if self.ctype in ("R", "D"):
                Line(points=[-60, 0, -44, 0], width=2)
                Line(points=[44, 0, 60, 0], width=2)
            else:
                Line(points=[0, 22, 0, 40], width=2)
                Line(points=[0, -22, 0, -40], width=2)

            # símbolo
            if self.ctype == "R":
                Color(1.0, 0.35, 0.35, 1)
                amp, seg, L = 8, 6, 88
                x0 = -L / 2
                pts = [x0, 0]
                for i in range(seg):
                    x1 = x0 + (L / seg) / 2
                    y1 = amp if i % 2 == 0 else -amp
                    x2 = x0 + (L / seg)
                    pts += [x1, y1, x2, 0]
                    x0 = x2
                Line(points=pts, width=2)
            elif self.ctype == "V":
                Color(0.96, 0.96, 0.99, 1)
                Ellipse(pos=(-16, -16), size=(32, 32))
                Color(0.12, 0.82, 0.72, 1)
                Line(points=[-5, 0, 5, 0], width=2)
                Line(points=[0, -5, 0, 5], width=2)
                Color(0.86, 0.25, 0.25, 1)
                Line(points=[-5, -10, 5, -10], width=2)
            else:
                Color(0.96, 0.96, 0.99, 1)
                Triangle(points=[-26, -10, -26, 10, 0, 0])
                Line(points=[2, -10, 2, 10], width=2)
            PopMatrix()

            # pines amarillos
            Color(0.93, 0.78, 0.18, 1)
            for _, (px, py) in self.pin_world().items():
                Ellipse(pos=(px - PIN_R / 2, py - PIN_R / 2),
                        size=(PIN_R, PIN_R))

            # etiqueta
            from kivy.core.text import Label as CoreLabel
            Color(0.86, 0.90, 1, 1)
            lab = CoreLabel(text=self.cid, font_size=14)
            lab.refresh()
            Rectangle(texture=lab.texture,
                      pos=(self.x + 4, self.top - 18),
                      size=lab.texture.size)


class Junction(Widget):
    jid = StringProperty("")
    selected = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.size_hint = (None, None)
        self.size = (GRID, GRID)
        self._drag = False
        self._last_tap = 0.0
        self.bind(pos=self._redraw, size=self._redraw, selected=self._redraw)
        Clock.schedule_once(lambda *_: self._redraw(), 0)

    def collide_point(self, x, y):
        return (abs(x - self.center_x) <= 10) and (abs(y - self.center_y) <= 10)

    def on_touch_down(self, touch):
        parent = self.parent
        if parent and getattr(parent, "mode", "") == "wire":
            return False
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        
        now = time.time()
        if now - self._last_tap < 0.35:
            # Doble clic en junction (sin propiedades por ahora)
            self._last_tap = 0
            return True
        self._last_tap = now
        
        if hasattr(parent, "select_junction"):
            parent.select_junction(self)
        self._drag = True
        self._dx = touch.x - self.x
        self._dy = touch.y - self.y
        App.get_running_app().set_status(f"Seleccionado: {self.jid}")
        return True

    def on_touch_move(self, touch):
        if not self._drag:
            return super().on_touch_move(touch)
        nx = round((touch.x - self._dx) / GRID) * GRID
        ny = round((touch.y - self._dy) / GRID) * GRID
        self.pos = (nx, ny)
        if hasattr(self.parent, "redraw_wires"):
            self.parent.redraw_wires()
        return True

    def on_touch_up(self, touch):
        if not self._drag:
            return super().on_touch_up(touch)
        self._drag = False
        if hasattr(self.parent, "redraw_wires"):
            self.parent.redraw_wires()
        return True

    def world(self):
        return self.center

    def _redraw(self, *_):
        self.canvas.clear()
        with self.canvas:
            if self.selected:
                Color(1, 1, 1, 0.10)
                Rectangle(pos=(self.x - 6, self.y - 6),
                          size=(self.width + 12, self.height + 12))
            Color(0.93, 0.78, 0.18, 1)
            Ellipse(pos=(self.center_x - 5, self.center_y - 5), size=(10, 10))


@dataclass
class Wire:
    a: Tuple[str, str]
    b: Tuple[str, str]
    gfx: Optional[InstructionGroup] = None
    pts: Optional[List[float]] = None


# -------------------------------------------------
#  CANVAS PRINCIPAL
# -------------------------------------------------
class CircuitCanvas(FloatLayout):
    mode = StringProperty("select")
    selected: Optional[CompWidget] = None
    selected_j: Optional[Junction] = None

    def __init__(self, **kw):
        super().__init__(**kw)
        Clock.schedule_once(self._setup, 0.05)
        self.components: Dict[str, CompWidget] = {}
        self.junctions: Dict[str, Junction] = {}
        self._idc = {"R": 1, "V": 1, "D": 1, "J": 1}
        self.wires: List[Wire] = []
        self._wire_first: Optional[Tuple[str, str]] = None
        self._ghost: Optional[InstructionGroup] = None
        self._gnd: Optional[Tuple[str, str]] = None

    def _setup(self, *_):
        self.bind(size=self._grid, pos=self._grid)
        self._grid()

    def _grid(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.08, 0.10, 0.13, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(1, 1, 1, 0.055)
            x0, y0 = self.pos
            w, h = self.size
            for i in range(int(w // GRID) + 1):
                x = x0 + i * GRID
                Line(points=[x, y0, x, y0 + h], width=1)
            for j in range(int(h // GRID) + 1):
                y = y0 + j * GRID
                Line(points=[x0, y, x0 + w, y], width=1)

    # --------------- selección ---------------
    def select_component(self, cw):
        if self.selected and self.selected is not cw:
            self.selected.selected = False
        if self.selected_j:
            self.selected_j.selected = False
            self.selected_j = None
        self.selected = cw
        cw.selected = True
        App.get_running_app().update_inspector(cw)

    def select_junction(self, j):
        if self.selected:
            self.selected.selected = False
            self.selected = None
        if self.selected_j and self.selected_j is not j:
            self.selected_j.selected = False
        self.selected_j = j
        j.selected = True
        app = App.get_running_app()
        app.root.ids.ins_name.text = j.jid
        app.root.ids.ins_value.text = "-"
        app.root.ids.ins_desc.text = DESC["J"]

    def set_mode(self, m):
        self.mode = m
        self._wire_first = None
        self._clear_ghost()
        names = {
            "select": "Seleccionar",
            "add_R": "Agregar Resistor",
            "add_V": "Agregar Fuente",
            "add_D": "Agregar Diodo",
            "add_J": "Agregar Nodo",
            "wire": "Conectar con Cable",
            "set_gnd": "Establecer Tierra (GND)",
        }
        App.get_running_app().set_status(f"Modo: {names.get(m, m)}")

    # --------------- altas/bajas ---------------
    def add_component(self, t):
        cid = f"{t}{self._idc[t]}"
        self._idc[t] += 1
        props = {
            "R": {"R": 1000.0},
            "V": {"V": 5.0},
            "D": {"polarity": "A_to_K"},
        }.get(t, {})
        cw = CompWidget(cid=cid, ctype=t, props=props)
        cw.center = snap((self.center_x, self.center_y))
        self.add_widget(cw)
        self.components[cid] = cw
        self.select_component(cw)

    def add_junction(self):
        jid = f"J{self._idc['J']}"
        self._idc["J"] += 1
        j = Junction(jid=jid)
        j.center = snap((self.center_x, self.center_y))
        self.add_widget(j)
        self.junctions[jid] = j
        self.select_junction(j)

    def rotate_selected(self):
        if not self.selected:
            return
        self.selected.rot = (self.selected.rot + 90) % 360
        self.redraw_wires()
        App.get_running_app().update_inspector(self.selected)

    def delete_selected(self):
        if self.selected:
            cid = self.selected.cid
            to_keep = []
            for w in self.wires:
                if w.a[0] == cid or w.b[0] == cid:
                    if w.gfx:
                        self.canvas.after.remove(w.gfx)
                else:
                    to_keep.append(w)
            self.wires = to_keep
            self.remove_widget(self.selected)
            self.components.pop(cid, None)
            self.selected = None
        elif self.selected_j:
            jid = self.selected_j.jid
            to_keep = []
            for w in self.wires:
                if w.a[0] == f"J:{jid}" or w.b[0] == f"J:{jid}":
                    if w.gfx:
                        self.canvas.after.remove(w.gfx)
                else:
                    to_keep.append(w)
            self.wires = to_keep
            self.remove_widget(self.selected_j)
            self.junctions.pop(jid, None)
            self.selected_j = None
        App.get_running_app().set_status("Elemento y cables asociados eliminados.")
        self._clear_ghost()

    # --------------- utilería ---------------
    def _hit_pin(self, x, y) -> Optional[Tuple[str, str]]:
        # Primero verificar junctions (prioridad)
        for jid, j in self.junctions.items():
            px, py = j.world()
            if (x - px) ** 2 + (y - py) ** 2 <= (12) ** 2:
                return (f"J:{jid}", "J")
        
        # Luego pines de componentes
        for cid, cw in self.components.items():
            for pname, (px, py) in cw.pin_world().items():
                if (x - px) ** 2 + (y - py) ** 2 <= (PIN_R * 1.5) ** 2:
                    return (cid, pname)
        return None

    def _get_obstacles(self) -> set:
        """Obtiene obstáculos para el pathfinding (simplificado)"""
        occ = set()
        
        # Solo marcar el centro de los componentes como obstáculo
        for cw in self.components.values():
            cx, cy = snap(cw.center)
            # Área reducida alrededor del componente
            for dx in range(-GRID*2, GRID*3, GRID):
                for dy in range(-GRID, GRID*2, GRID):
                    occ.add((cx + dx, cy + dy))
        
        return occ

    def _find_path_simple(self, p1, p2) -> List[float]:
        """Pathfinding simplificado: línea recta con esquinas ortogonales"""
        x1, y1 = snap(p1)
        x2, y2 = snap(p2)
        
        # Si están alineados horizontalmente o verticalmente, línea directa
        if x1 == x2 or y1 == y2:
            return [x1, y1, x2, y2]
        
        # Sino, crear dos segmentos ortogonales
        # Probar ruta horizontal-vertical
        mid_x = x2
        mid_y = y1
        return [x1, y1, mid_x, mid_y, x2, y2]

    def _find_path_astar(self, p1, p2) -> Optional[List[float]]:
        """A* mejorado con mejor heurística"""
        start, goal = snap(p1), snap(p2)
        
        # Primero intentar ruta simple
        simple_path = self._find_path_simple(p1, p2)
        
        # Verificar si hay obstáculos en la ruta simple
        occ = self._get_obstacles()
        path_clear = True
        for i in range(0, len(simple_path) - 2, 2):
            x, y = snap((simple_path[i], simple_path[i + 1]))
            if (x, y) in occ and (x, y) != start and (x, y) != goal:
                path_clear = False
                break
        
        if path_clear:
            return simple_path
        
        # Si hay obstáculos, usar A* pero con límite de iteraciones
        def h(a, b): 
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        open_set = []
        heapq.heappush(open_set, (h(start, goal), 0, start, [start]))
        visited = {start}
        iterations = 0
        max_iterations = 200  # Límite para evitar cuelgues
        
        while open_set and iterations < max_iterations:
            iterations += 1
            f, g, pos, path = heapq.heappop(open_set)
            
            if pos == goal:
                return [c for pt in path for c in pt]
            
            for dx, dy in [(0, GRID), (0, -GRID), (GRID, 0), (-GRID, 0)]:
                nb = (pos[0] + dx, pos[1] + dy)
                if not (self.x <= nb[0] <= self.right and self.y <= nb[1] <= self.top):
                    continue
                if nb in visited:
                    continue
                if nb in occ and nb != goal:
                    continue
                    
                visited.add(nb)
                g2 = g + GRID
                f2 = g2 + h(nb, goal)
                heapq.heappush(open_set, (f2, g2, nb, path + [nb]))
        
        # Si A* falla, devolver ruta simple de todos modos
        return simple_path

    def _clear_ghost(self):
        if self._ghost:
            self.canvas.after.remove(self._ghost)
            self._ghost = None

    def _pin_world(self, comp_id, pin):
        if comp_id.startswith("J:"):
            jid = comp_id.split(":", 1)[1]
            return self.junctions[jid].world()
        return self.components[comp_id].pin_world()[pin]

    def _update_ghost(self, cursor_pos):
        if not self._wire_first or cursor_pos is None:
            self._clear_ghost()
            return
        p1 = self._pin_world(*self._wire_first)
        hit = self._hit_pin(*cursor_pos)
        self._clear_ghost()
        self._ghost = InstructionGroup()
        
        if not hit:
            # Línea punteada al cursor
            self._ghost.add(Color(1.0, 0.3, 0.3, 0.6))
            self._ghost.add(
                Line(points=[p1[0], p1[1], cursor_pos[0], cursor_pos[1]],
                     width=2, dash_length=4)
            )
            self.canvas.after.add(self._ghost)
            return
        
        p2 = self._pin_world(*hit)
        pts = self._find_path_astar(p1, p2)
        if pts:
            self._ghost.add(Color(0.25, 0.7, 1.0, 0.8))
            self._ghost.add(Line(points=pts, width=3, cap="round"))
        else:
            self._ghost.add(Color(1.0, 0.2, 0.2, 0.7))
            self._ghost.add(
                Line(points=[p1[0], p1[1], p2[0], p2[1]], width=2, dash_length=4)
            )
        self.canvas.after.add(self._ghost)

    # --------------- interacción ---------------
    def on_touch_down(self, touch):
        if super().on_touch_down(touch):
            return True
        if not self.collide_point(*touch.pos):
            return False

        if self.mode in ("add_R", "add_V", "add_D"):
            self.add_component(self.mode.split("_")[1])
            App.get_running_app().set_status(
                "Componente agregado. Arrastra para mover o doble-clic para editar."
            )
            return True

        if self.mode == "add_J":
            self.add_junction()
            App.get_running_app().set_status("Nodo agregado. Úsalo para conectar múltiples cables.")
            return True

        if self.mode == "wire":
            hit = self._hit_pin(*touch.pos)
            if not hit:
                App.get_running_app().set_status(
                    "⚠️ Haz clic en un pin (círculo dorado) o nodo (punto amarillo)"
                )
                return True
            if not self._wire_first:
                self._wire_first = hit
                App.get_running_app().set_status(
                    f"✓ Primer punto: {hit[0]}. Ahora selecciona el segundo punto."
                )
                return True
            a = self._wire_first
            b = hit
            if a != b:
                self._add_wire(a, b)
                App.get_running_app().set_status(
                    f"✓ Cable conectado: {a[0]} → {b[0]}"
                )
            else:
                App.get_running_app().set_status(
                    "❌ No puedes conectar un pin consigo mismo."
                )
            self._wire_first = None
            self._clear_ghost()
            return True

        if self.mode == "set_gnd":
            hit = self._hit_pin(*touch.pos)
            if not hit:
                App.get_running_app().set_status("⚠️ Selecciona un pin o nodo para establecer como GND.")
                return True
            self._gnd = hit
            App.get_running_app().set_status(f"✓ Tierra (GND) establecida en: {hit[0]}")
            return True

        return False

    def on_touch_move(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_move(touch)
        if self.mode == "wire" and self._wire_first:
            self._update_ghost(touch.pos)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_up(touch)
        return super().on_touch_up(touch)

    # --------------- cables ---------------
    def _add_wire(self, a, b):
        p1 = self._pin_world(*a)
        p2 = self._pin_world(*b)
        pts = self._find_path_astar(p1, p2)
        if not pts:
            App.get_running_app().set_status(
                "⚠️ No se pudo trazar el cable. Intenta reposicionar los componentes."
            )
            return
        w = Wire(a, b, pts=pts)
        self._draw_wire(w)
        self.wires.append(w)

    def _draw_wire(self, w):
        if w.gfx:
            self.canvas.after.remove(w.gfx)
        if not w.pts:
            return
        grp = InstructionGroup()
        grp.add(Color(0.02, 0.03, 0.05, 1))
        grp.add(Line(points=w.pts, width=4, cap="round", joint="miter"))
        grp.add(Color(0.35, 0.80, 1.0, 1))
        grp.add(Line(points=w.pts, width=2.2, cap="round", joint="miter"))
        self.canvas.after.add(grp)
        w.gfx = grp

    def redraw_wires(self):
        for w in self.wires:
            try:
                new_pts = self._find_path_astar(
                    self._pin_world(*w.a),
                    self._pin_world(*w.b)
                )
                if new_pts:
                    w.pts = new_pts
                self._draw_wire(w)
            except Exception:
                # Si hay error al redibujar, mantener puntos anteriores
                pass

    # --------------- conectividad ---------------
    def _connectivity_ok(self) -> Tuple[bool, str]:
        """Verifica que el circuito esté correctamente conectado"""
        if not self.components:
            return False, "⚠️ Añade al menos un componente para simular."
        
        uf = UF()
        
        # Unir todos los puntos conectados por cables
        for w in self.wires:
            uf.union(f"{w.a[0]}:{w.a[1]}", f"{w.b[0]}:{w.b[1]}")

        # Recopilar todos los pines
        pins: List[Tuple[str, str]] = []
        for cid, cw in self.components.items():
            for pname in cw.pin_world().keys():
                pins.append((cid, pname))
        for jid in self.junctions.keys():
            pins.append((f"J:{jid}", "J"))
        
        if not pins:
            return False, "⚠️ Añade componentes al circuito."

        # Agrupar pines conectados
        groups: Dict[str, List[Tuple[str, str]]] = {}
        for cid, pn in pins:
            r = uf.find(f"{cid}:{pn}")
            groups.setdefault(r, []).append((cid, pn))

        # Asignar nombres a los nodos
        names: Dict[str, str] = {}
        k = 1
        if self._gnd:
            gnd_key = uf.find(f"{self._gnd[0]}:{self._gnd[1]}")
            names[gnd_key] = "GND"
        
        # Si no hay GND definido, usar el primer grupo
        if "GND" not in names.values() and groups:
            first_key = next(iter(groups.keys()))
            names[first_key] = "GND"
            
        for r in groups.keys():
            if r not in names:
                names[r] = f"N{k}"
                k += 1

        # Construir grafo de conectividad entre nodos
        adj: Dict[str, set] = {names[r]: set() for r in groups.keys()}
        used_nodes: set = set()

        for cid, cw in self.components.items():
            pin2node: Dict[str, str] = {}
            for pname in cw.pin_world().keys():
                root = uf.find(f"{cid}:{pname}")
                pin2node[pname] = names[root]
                used_nodes.add(names[root])

            if cw.ctype == "R":
                n1, n2 = pin2node["A"], pin2node["B"]
            elif cw.ctype == "V":
                n1, n2 = pin2node["+"], pin2node["-"]
            else:  # Diodo
                n1, n2 = pin2node["A"], pin2node["K"]

            if n1 != n2:  # Solo agregar si son nodos diferentes
                adj.setdefault(n1, set()).add(n2)
                adj.setdefault(n2, set()).add(n1)

        if not used_nodes:
            return False, "⚠️ Conecta los componentes con cables antes de simular."

        # Verificar que GND esté definido
        if "GND" not in used_nodes:
            return False, "⚠️ Define un nodo como tierra (GND) usando el botón 'Tierra (GND)'."

        # Verificar conectividad desde GND
        visited = set()
        stack = ["GND"]
        while stack:
            u = stack.pop()
            if u in visited:
                continue
            visited.add(u)
            for v in adj.get(u, []):
                if v not in visited:
                    stack.append(v)

        not_reached = used_nodes - visited
        if not_reached:
            listado = ", ".join(sorted(not_reached))
            return False, (
                f"⚠️ Hay nodos aislados sin conexión a GND: {listado}. "
                "Verifica que todos los componentes estén conectados."
            )
        
        return True, "✓ Circuito conectado correctamente."

    # --------------- netlist ---------------
    def build_netlist(self) -> Netlist:
        """Construye el netlist desde el canvas"""
        uf = UF()
        
        # Unir puntos conectados por cables
        for w in self.wires:
            uf.union(f"{w.a[0]}:{w.a[1]}", f"{w.b[0]}:{w.b[1]}")

        # Recopilar pines
        pins = []
        for cid, cw in self.components.items():
            for pname in cw.pin_world().keys():
                pins.append((cid, pname))
        for jid in self.junctions.keys():
            pins.append((f"J:{jid}", "J"))

        # Agrupar por conectividad
        groups = {}
        for cid, pn in pins:
            r = uf.find(f"{cid}:{pn}")
            groups.setdefault(r, []).append((cid, pn))

        # Asignar nombres a nodos
        names = {}
        k = 1
        if self._gnd:
            gnd_key = uf.find(f"{self._gnd[0]}:{self._gnd[1]}")
            names[gnd_key] = "GND"
        
        if "GND" not in names.values() and groups:
            first_key = next(iter(groups.keys()))
            names[first_key] = "GND"
            
        for r in groups.keys():
            if r not in names:
                names[r] = f"N{k}"
                k += 1

        # Crear netlist
        nl = Netlist()
        for r in groups.keys():
            nl.add_node(names[r], is_ground=(names[r] == "GND"))

        # Agregar componentes
        for cid, cw in self.components.items():
            mp = {pn: names[uf.find(f"{cid}:{pn}")] for pn in cw.pin_world().keys()}
            
            if cw.ctype == "R":
                nl.add_component(
                    Resistor(
                        id=cid, n1=mp["A"], n2=mp["B"],
                        R=float(cw.props.get("R", 1000.0))
                    )
                )
            elif cw.ctype == "V":
                nl.add_component(
                    VSource(
                        id=cid, n1=mp["+"], n2=mp["-"],
                        V=float(cw.props.get("V", 5.0))
                    )
                )
            else:  # Diodo
                nl.add_component(
                    IdealDiode(
                        id=cid, n1=mp["A"], n2=mp["K"],
                        polarity=cw.props.get("polarity", "A_to_K"),
                    )
                )
        return nl

    # --------------- acciones ---------------
    def simulate_from_canvas(self):
        try:
            ok, msg = self._connectivity_ok()
            app = App.get_running_app()
            if not ok:
                app.root.ids.lbl_info.text = f"[color=#ff6666]{msg}[/color]"
                app.set_status(msg)
                return
            
            nl = self.build_netlist()
            sol = simulate(nl)
            app.show_results(sol)
            app.set_status("✓ Simulación completada exitosamente.")
        except Exception as e:
            app = App.get_running_app()
            error_msg = f"❌ Error: {str(e)}"
            app.set_status(error_msg)
            app.info_popup("Error de simulación", str(e))

    def export_pdf_from_canvas(self):
        """Exporta el circuito y resultados a PDF"""
        try:
            ok, msg = self._connectivity_ok()
            app = App.get_running_app()
            if not ok:
                app.root.ids.lbl_info.text = f"[color=#ff6666]{msg}[/color]"
                app.set_status(msg)
                return

            nl = self.build_netlist()
            sol = simulate(nl)

            # Preparar datos de solución
            solution = {
                "node_voltages": dict(getattr(sol, "node_voltages", {})),
                "branch_currents": dict(getattr(sol, "branch_currents", {})),
                "kcl": {},
                "kvl": {},
                "netlist": self._netlist_to_dict(nl),
            }
            
            # Extraer checks si existen
            if hasattr(sol, "checks") and sol.checks:
                kcl_data = sol.checks.get("KCL", {})
                kvl_data = sol.checks.get("KVL", {})
                solution["kcl"] = {k: v.get("ok", False) for k, v in kcl_data.items()}
                solution["kvl"] = {k: v.get("ok", False) for k, v in kvl_data.items()}

            # Capturar imagen del canvas
            tmp_path = os.path.join(tempfile.gettempdir(), "cirkit_canvas.png")
            self.export_to_png(tmp_path)
            with open(tmp_path, "rb") as f:
                png_b64 = base64.b64encode(f.read()).decode("ascii")
            try:
                os.remove(tmp_path)
            except OSError:
                pass

            # Diálogo para guardar
            root = tk.Tk()
            root.withdraw()
            fname = filedialog.asksaveasfilename(
                title="Guardar reporte PDF",
                defaultextension=".pdf",
                filetypes=[("Archivo PDF", "*.pdf")],
                initialfile="reporte_cirkit.pdf",
            )
            root.destroy()
            
            if not fname:
                app.set_status("Exportación cancelada.")
                return

            export_solution_pdf(fname, solution, diagram=self.to_json(), png_b64=png_b64)
            app.info_popup("PDF Exportado", f"Reporte guardado exitosamente en:\n{fname}")
            app.set_status("✓ PDF exportado correctamente.")
        except Exception as e:
            app = App.get_running_app()
            app.set_status(f"❌ Error al exportar: {e}")
            app.info_popup("Error de exportación", str(e))

    def _netlist_to_dict(self, nl: Netlist) -> Dict[str, Any]:
        """Convierte netlist a diccionario para exportación"""
        elements = []
        for c in nl.components:
            elem = {
                "type": c.kind,
                "name": c.id,
                "a": c.n1,
                "b": c.n2,
            }
            if c.kind == "R":
                elem["value"] = c.R
            elif c.kind == "V":
                elem["value"] = c.V
            elements.append(elem)
        return {"elements": elements}

    # --------------- serialización ---------------
    def to_json(self) -> Dict[str, Any]:
        return {
            "components": [
                {
                    "id": cw.cid, 
                    "type": cw.ctype,
                    "x": float(cw.center_x), 
                    "y": float(cw.center_y),
                    "rot": int(cw.rot), 
                    "props": dict(cw.props),
                }
                for cw in self.components.values()
            ],
            "junctions": [
                {
                    "id": jid, 
                    "x": float(j.center_x), 
                    "y": float(j.center_y)
                }
                for jid, j in self.junctions.items()
            ],
            "wires": [{"a": w.a, "b": w.b} for w in self.wires],
            "gnd": self._gnd,
        }

    def from_json(self, data: Dict[str, Any]):
        # Limpiar canvas
        for w in self.wires:
            if w.gfx:
                self.canvas.after.remove(w.gfx)
        for cw in list(self.components.values()):
            self.remove_widget(cw)
        for j in list(self.junctions.values()):
            self.remove_widget(j)
        self.wires.clear()
        self.components.clear()
        self.junctions.clear()
        self.selected = None
        self.selected_j = None
        self._gnd = None
        self._idc = {"R": 1, "V": 1, "D": 1, "J": 1}

        # Cargar componentes con sus posiciones originales
        for c in data.get("components", []):
            cw = CompWidget(
                cid=c["id"], 
                ctype=c["type"],
                rot=int(c.get("rot", 0)),
                props=c.get("props", {}),
            )
            # IMPORTANTE: usar las coordenadas guardadas
            cw.center_x = c.get("x", self.center_x)
            cw.center_y = c.get("y", self.center_y)
            self.add_widget(cw)
            self.components[cw.cid] = cw
            
            # Actualizar contador
            t = cw.ctype
            try:
                n = int("".join(filter(str.isdigit, cw.cid)))
            except Exception:
                n = 0
            self._idc[t] = max(self._idc[t], n + 1)

        # Cargar junctions con sus posiciones originales
        for j in data.get("junctions", []):
            jj = Junction(jid=j["id"])
            # IMPORTANTE: usar las coordenadas guardadas
            jj.center_x = j.get("x", self.center_x)
            jj.center_y = j.get("y", self.center_y)
            self.add_widget(jj)
            self.junctions[jj.jid] = jj
            
            # Actualizar contador
            try:
                n = int("".join(filter(str.isdigit, jj.jid)))
            except Exception:
                n = 0
            self._idc["J"] = max(self._idc["J"], n + 1)

        # Cargar cables con delay para asegurar que los widgets estén listos
        def cargar_cables(*args):
            for w in data.get("wires", []):
                try:
                    self._add_wire(tuple(w["a"]), tuple(w["b"]))
                except Exception as e:
                    print(f"Error al cargar cable: {e}")
            self.redraw_wires()
        
        # Usar Clock para dar tiempo a que se rendericen los widgets
        Clock.schedule_once(cargar_cables, 0.1)

        # Cargar GND
        gnd_data = data.get("gnd")
        self._gnd = tuple(gnd_data) if isinstance(gnd_data, list) else gnd_data

        App.get_running_app().set_status("✓ Diagrama cargado correctamente.")

    # --------------- propiedades ---------------
    def open_properties(self, cw: "CompWidget"):
        box = BoxLayout(orientation="vertical", spacing=8, padding=10)
        title_map = {"R": "Resistor", "V": "Fuente de Voltaje", "D": "Diodo"}
        title = title_map.get(cw.ctype, "Componente")

        if cw.ctype == "R":
            ti = TextInput(
                text=str(cw.props.get("R", 1000.0)),
                multiline=False, input_filter="float",
            )
            box.add_widget(Label(text="Resistencia (Ω):"))
            box.add_widget(ti)
            target = ti
        elif cw.ctype == "V":
            ti = TextInput(
                text=str(cw.props.get("V", 5.0)),
                multiline=False, input_filter="float",
            )
            box.add_widget(Label(text="Voltaje (V):"))
            box.add_widget(ti)
            target = ti
        else:
            sp = Spinner(
                text=cw.props.get("polarity", "A_to_K"),
                values=["A_to_K", "K_to_A"],
            )
            box.add_widget(Label(text="Polaridad:"))
            box.add_widget(sp)
            target = sp

        btns = BoxLayout(size_hint_y=None, height="42dp", spacing=8)

        def _ok(*_):
            try:
                if cw.ctype == "R":
                    cw.props["R"] = float(target.text)
                elif cw.ctype == "V":
                    cw.props["V"] = float(target.text)
                else:
                    cw.props["polarity"] = target.text
                p.dismiss()
                App.get_running_app().update_inspector(cw)
                cw._redraw()
            except Exception as e:
                App.get_running_app().set_status(f"❌ Error: {e}")

        b1 = Button(text="Aceptar")
        b1.bind(on_release=_ok)
        b2 = Button(text="Cancelar")
        b2.bind(on_release=lambda *_: p.dismiss())
        btns.add_widget(b1)
        btns.add_widget(b2)
        box.add_widget(btns)
        
        p = Popup(
            title=f"Propiedades: {title} {cw.cid}",
            content=box, size_hint=(0.5, 0.4),
        )
        p.open()


# -------------------------------------------------
#  APP
# -------------------------------------------------
class Contenedor_01(BoxLayout):
    pass


def _base_assets_dir() -> pathlib.Path:
    """Devuelve carpeta base de recursos tanto en dev como en build (.exe)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return pathlib.Path(meipass)
    return pathlib.Path(__file__).resolve().parents[2]


def _icons_dir() -> pathlib.Path:
    base = _base_assets_dir()
    candidates = [
        base / "kivy" / "icons",
        base / "icons",
        ROOT / "src" / "ui" / "kivy" / "icons",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


class CirKitApp(App):
    title = "CirKit - Simulador de Circuitos Eléctricos"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.kv_file = ""

        # Recursos (solo app icon)
        icons = _icons_dir()
        resource_add_path(str(icons))
        try:
            ico = icons / "app_icon.ico"
            png = icons / "app_icon.png"
            if ico.exists():
                Window.set_icon(str(ico))
            elif png.exists():
                Window.set_icon(str(png))
        except Exception:
            pass

        # Tamaño/estado de ventana
        try:
            Window.minimum_width  = 1100
            Window.minimum_height = 650
            if Window.width < 1100 or Window.height < 650:
                Window.size = (1280, 780)
            Window.maximize()
        except Exception:
            pass

    def build(self):
        kv_path = str(pathlib.Path(__file__).with_suffix(".kv"))
        return Builder.load_file(kv_path)

    # -------- Helpers seguros --------
    def _get_root_widget(self):
        return self.root

    def _get_ids(self) -> Dict[str, Any]:
        r = self._get_root_widget()
        return getattr(r, "ids", {})

    # ----------------------------------
    def set_status(self, txt: str) -> None:
        ids = self._get_ids()
        lbl = ids.get("status_label")
        if lbl is not None:
            lbl.text = txt

    def update_inspector(self, cw: 'CompWidget') -> None:
        ids = self._get_ids()
        ins_name = ids.get("ins_name")
        ins_value = ids.get("ins_value")
        ins_desc = ids.get("ins_desc")

        if ins_name is None or ins_value is None or ins_desc is None:
            return

        ins_name.text = cw.cid
        if cw.ctype == "R":
            val = f"{cw.props.get('R', 1000.0)} Ω"
        elif cw.ctype == "V":
            val = f"{cw.props.get('V', 5.0)} V"
        else:
            val = cw.props.get("polarity", "A_to_K")
        ins_value.text = val
        ins_desc.text = DESC.get(cw.ctype, "")

    def show_results(self, sol) -> None:
        ids = self._get_ids()
        lbl = ids.get("lbl_info")
        if lbl is None:
            return
        lines = ["[b][color=#4CAF50]=== RESULTADOS ===[/color][/b]", "", "[b]Voltajes nodales:[/b]"]
        for k, v in sol.node_voltages.items():
            lines.append(f"  [color=#90CAF9]•[/color] {k}: [b]{v:.6f} V[/b]")
        lines += ["", "[b]Corrientes de rama:[/b]"]
        for k, i in sol.branch_currents.items():
            lines.append(f"  [color=#90CAF9]•[/color] {k}: [b]{i:.9f} A[/b]")
        lbl.markup = True
        lbl.text = "\n".join(lines)

    def info_popup(self, title: str, msg: str) -> None:
        content = Label(text=str(msg), halign="left", valign="middle")
        pop = Popup(title=title, content=content, size_hint=(0.6, 0.5))
        content.bind(
            size=lambda *_: setattr(content, "text_size", (content.width - 20, None))
        )
        pop.open()

    # ---------------- Guardar / Abrir ----------------
    def prompt_save(self) -> None:
        ids = self._get_ids()
        canvas = ids.get("canvas_area")
        if canvas is None:
            self.info_popup("Error", "No se encontró el canvas para guardar.")
            return
        try:
            root = tk.Tk()
            root.withdraw()
            fname = filedialog.asksaveasfilename(
                title="Guardar diagrama (.json)",
                defaultextension=".json",
                filetypes=[("Diagramas CirKit", "*.json")],
                initialfile="diagrama.json",
            )
            root.destroy()
            if not fname:
                self.set_status("Guardado cancelado.")
                return
            data = canvas.to_json()
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.info_popup("Guardar", f"Diagrama guardado en:\n{fname}")
            self.set_status("✓ Diagrama guardado correctamente.")
        except Exception as e:
            self.info_popup("Error al Guardar", str(e))

    def prompt_open(self) -> None:
        ids = self._get_ids()
        canvas = ids.get("canvas_area")
        if canvas is None:
            self.info_popup("Error", "No se encontró el canvas para abrir.")
            return
        try:
            root = tk.Tk()
            root.withdraw()
            fname = filedialog.askopenfilename(
                title="Abrir diagrama (.json)",
                filetypes=[("Diagramas CirKit", "*.json"), ("Todos los archivos", "*.*")],
            )
            root.destroy()
            if not fname:
                self.set_status("Apertura cancelada.")
                return
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            canvas.from_json(data)
            self.info_popup("Abrir", f"Diagrama cargado desde:\n{fname}")
        except Exception as e:
            self.info_popup("Error al Abrir", str(e))

    # ---------------- Tutorial + Plantillas ----------------
    def open_tutorial_popup(self, *_):
        ids = self._get_ids()
        if not ids:
            return

        box = BoxLayout(orientation="vertical", spacing=12, padding=16)
        
        # Título con estilo
        title_lbl = Label(
            text="[b][size=20]Guía Rápida de CirKit[/size][/b]",
            markup=True,
            size_hint_y=None,
            height="40dp"
        )
        box.add_widget(title_lbl)
        
        # Instrucciones detalladas
        tips = (
            "[b]Pasos para construir tu circuito:[/b]\n\n"
            "[color=#4CAF50]1. Agregar componentes:[/color]\n"
            "   • Haz clic en 'Resistor (R)', 'Fuente (V)' o 'Diodo (D)'\n"
            "   • El componente aparecerá en el centro del canvas\n"
            "   • Arrástralo a la posición deseada\n\n"
            "[color=#4CAF50]2. Editar valores:[/color]\n"
            "   • Haz [b]doble clic[/b] sobre un componente\n"
            "   • Cambia su resistencia, voltaje o polaridad\n\n"
            "[color=#4CAF50]3. Conectar con cables:[/color]\n"
            "   • Selecciona 'Cable' en la barra de herramientas\n"
            "   • Haz clic en el primer pin (círculo dorado)\n"
            "   • Luego haz clic en el segundo pin\n"
            "   • El cable se trazará automáticamente\n\n"
            "[color=#4CAF50]4. Usar nodos (•):[/color]\n"
            "   • Los nodos sirven para conectar 3 o más cables\n"
            "   • Agrega un nodo y conéctalo con 'Cable'\n\n"
            "[color=#4CAF50]5. Establecer tierra (GND):[/color]\n"
            "   • Selecciona 'Tierra (GND)' en la barra\n"
            "   • Haz clic en el nodo de referencia\n"
            "   • Es necesario para la simulación\n\n"
            "[color=#4CAF50]6. Simular:[/color]\n"
            "   • Presiona 'Simular' para calcular voltajes y corrientes\n"
            "   • Los resultados aparecerán en el panel derecho\n\n"
            "[color=#2196F3][b]¡Prueba las plantillas de ejemplo para aprender más rápido![/b][/color]"
        )
        
        lbl = Label(
            text=tips,
            markup=True,
            halign="left",
            valign="top",
            size_hint_y=None
        )
        lbl.bind(
            texture_size=lambda *_: setattr(lbl, "height", lbl.texture_size[1])
        )
        lbl.bind(
            size=lambda *_: setattr(lbl, "text_size", (lbl.width - 20, None))
        )
        
        from kivy.uix.scrollview import ScrollView
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(lbl)
        box.add_widget(scroll)

        # Separador
        sep_lbl = Label(
            text="[b]Plantillas de ejemplo:[/b]",
            markup=True,
            size_hint_y=None,
            height="30dp"
        )
        box.add_widget(sep_lbl)

        # Botones de plantillas
        def _tpl_btn(txt, desc, loader):
            btn_box = BoxLayout(orientation="vertical", size_hint_y=None, height="60dp", spacing=2)
            b = Button(text=txt, size_hint_y=None, height="35dp")
            b.bind(on_release=lambda *_: loader())
            desc_lbl = Label(text=f"[size=11]{desc}[/size]", markup=True, size_hint_y=None, height="20dp")
            btn_box.add_widget(b)
            btn_box.add_widget(desc_lbl)
            return btn_box

        grid = BoxLayout(orientation="vertical", size_hint_y=None, spacing=6)
        grid.bind(minimum_height=grid.setter("height"))
        
        grid.add_widget(_tpl_btn(
            "Circuito básico: V-R-GND",
            "Una fuente de voltaje conectada a una resistencia",
            lambda: self.load_template("plantilla_1")
        ))
        grid.add_widget(_tpl_btn(
            "Circuito con diodo: V-D-GND",
            "Fuente de voltaje con un diodo",
            lambda: self.load_template("plantilla_2")
        ))
        grid.add_widget(_tpl_btn(
            "Divisor de voltaje: V-R1-R2",
            "Dos resistencias en serie para dividir el voltaje",
            lambda: self.load_template("plantilla_3")
        ))
        grid.add_widget(_tpl_btn(
            "Circuito con nodo compartido",
            "Resistencia y diodo en paralelo usando un nodo común",
            lambda: self.load_template("plantilla_4")
        ))
        
        box.add_widget(grid)

        # Botón cerrar
        close_bar = BoxLayout(size_hint_y=None, height="48dp", spacing=8, padding=[0, 10, 0, 0])
        p = Popup(title="Tutorial de CirKit", content=box, size_hint=(0.85, 0.9))
        bclose = Button(text="Cerrar", size_hint_y=None, height="40dp")
        bclose.bind(on_release=lambda *_: p.dismiss())
        close_bar.add_widget(bclose)
        box.add_widget(close_bar)
        
        p.open()

    def load_template(self, name: str):
        ids = self._get_ids()
        canvas: CircuitCanvas = ids.get("canvas_area")  # type: ignore
        if not canvas:
            return

        # Limpiar canvas primero
        canvas.from_json({"components": [], "junctions": [], "wires": [], "gnd": None})

        # Esperar a que el canvas se renderice y obtener su centro real
        def crear_plantilla(*args):
            # Helper para agregar cables (con delay para asegurar que los widgets estén listos)
            cables_pendientes = []
            
            def place_comp(cid, ctype, props, x, y, rot=0):
                cw = CompWidget(cid=cid, ctype=ctype, props=props)
                cw.rot = rot
                # Asegurar que las coordenadas sean válidas
                cw.center_x = float(x)
                cw.center_y = float(y)
                canvas.add_widget(cw)
                canvas.components[cid] = cw
                # Actualizar contador
                try:
                    n = int("".join(filter(str.isdigit, cid)))
                    canvas._idc[ctype] = max(canvas._idc[ctype], n + 1)
                except:
                    pass
                return cw
                
            def place_junction(jid, x, y):
                j = Junction(jid=jid)
                # Asegurar que las coordenadas sean válidas
                j.center_x = float(x)
                j.center_y = float(y)
                canvas.add_widget(j)
                canvas.junctions[jid] = j
                # Actualizar contador
                try:
                    n = int("".join(filter(str.isdigit, jid)))
                    canvas._idc["J"] = max(canvas._idc["J"], n + 1)
                except:
                    pass
                return j
                
            def wire(a, ap, b, bp):
                # Guardar para procesar después
                aid = a.cid if hasattr(a, "cid") else f"J:{a.jid}"
                bid = b.cid if hasattr(b, "cid") else f"J:{b.jid}"
                cables_pendientes.append(((aid, ap), (bid, bp)))

            # Obtener centro del canvas
            cx = canvas.center_x if canvas.width > 0 else 640
            cy = canvas.center_y if canvas.height > 0 else 350
            
            if name == "plantilla_1":  # Circuito básico: V-R-GND
                v = place_comp("V1", "V", {"V": 5.0}, cx - 200, cy, 90)
                r = place_comp("R1", "R", {"R": 1000}, cx - 60, cy, 0)
                g = place_junction("J1", cx + 100, cy)
                
                wire(v, "+", r, "A")
                wire(r, "B", g, "J")
                wire(v, "-", g, "J")
                
                canvas._gnd = (f"J:{g.jid}", "J")
                
            elif name == "plantilla_2":  # Circuito con diodo: V-D-GND
                v = place_comp("V1", "V", {"V": 5.0}, cx - 200, cy, 90)
                d = place_comp("D1", "D", {"polarity": "A_to_K"}, cx - 60, cy, 0)
                g = place_junction("J1", cx + 100, cy)
                
                wire(v, "+", d, "A")
                wire(d, "K", g, "J")
                wire(v, "-", g, "J")
                
                canvas._gnd = (f"J:{g.jid}", "J")
                
            elif name == "plantilla_3":  # Divisor de voltaje: V con R1-R2 en serie
                v = place_comp("V1", "V", {"V": 12.0}, cx - 240, cy, 90)
                r1 = place_comp("R1", "R", {"R": 1000}, cx - 100, cy, 0)
                r2 = place_comp("R2", "R", {"R": 2000}, cx + 40, cy, 0)
                g = place_junction("J1", cx + 180, cy)
                
                wire(v, "+", r1, "A")
                wire(r1, "B", r2, "A")
                wire(r2, "B", g, "J")
                wire(v, "-", g, "J")
                
                canvas._gnd = (f"J:{g.jid}", "J")
                
            else:  # plantilla_4: Nodo común (R y D en paralelo)
                v = place_comp("V1", "V", {"V": 9.0}, cx - 260, cy, 90)
                jtop = place_junction("J1", cx - 60, cy + 30)
                jbot = place_junction("J2", cx - 60, cy - 30)
                r = place_comp("R1", "R", {"R": 470}, cx + 60, cy + 30, 0)
                d = place_comp("D1", "D", {"polarity": "A_to_K"}, cx + 60, cy - 30, 0)
                g = place_junction("J3", cx + 200, cy)
                
                wire(v, "+", jtop, "J")
                wire(jtop, "J", r, "A")
                wire(r, "B", g, "J")
                wire(v, "-", jbot, "J")
                wire(jbot, "J", d, "A")
                wire(d, "K", g, "J")
                
                canvas._gnd = (f"J:{g.jid}", "J")

            # Procesar cables después de que todos los widgets estén creados
            def agregar_cables(*args):
                for a, b in cables_pendientes:
                    try:
                        canvas._add_wire(a, b)
                    except Exception as e:
                        print(f"Error al agregar cable {a} -> {b}: {e}")
                canvas.redraw_wires()
            
            # Usar Clock.schedule_once para dar tiempo a que se rendericen los widgets
            Clock.schedule_once(agregar_cables, 0.15)
            
            # Mensajes descriptivos por plantilla
            messages = {
                "plantilla_1": "✓ Circuito básico cargado. Una fuente de 5V alimenta una resistencia de 1kΩ.",
                "plantilla_2": "✓ Circuito con diodo cargado. El diodo permite el flujo de corriente en una dirección.",
                "plantilla_3": "✓ Divisor de voltaje cargado. Las resistencias dividen el voltaje de 12V proporcionalmente.",
                "plantilla_4": "✓ Circuito con nodo común cargado. R y D comparten conexiones en paralelo.",
            }
            
            self.set_status(messages.get(name, f"✓ Plantilla {name} cargada."))
        
        # Dar tiempo a que el canvas se limpie y renderice
        Clock.schedule_once(crear_plantilla, 0.1)


# -------------------------------------------------
#  MAIN
# -------------------------------------------------
if __name__ == "__main__":
    CirKitApp().run()
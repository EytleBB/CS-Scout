# SPDX-FileCopyrightText: 2026 Pl4yer-ONE <mahadevan.rajeev27@gmail.com>
# SPDX-License-Identifier: LicenseRef-Sacrilege-EULA
#
# Sacrilege Engine - CS2 Demo Intelligence System
# https://github.com/Pl4yer-ONE/Sacrilege_Engine

"""
SACRILEGE RADAR - Ultimate CS2 Demo Replay Viewer
Professional visualization with full statistics and animations
"""

import pygame
import sys
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import Team, Vector3


@dataclass
class MapConfig:
    name: str
    display_name: str
    pos_x: float
    pos_y: float
    scale: float
    
    def world_to_radar(self, wx: float, wy: float, size: int) -> tuple:
        px = (wx - self.pos_x) / self.scale
        py = (self.pos_y - wy) / self.scale
        return int(max(0, min(size - 1, px))), int(max(0, min(size - 1, py)))


MAP_CONFIGS = {
    'de_mirage': MapConfig('de_mirage', 'MIRAGE', -3230, 1713, 5.0),
    'de_dust2': MapConfig('de_dust2', 'DUST II', -2476, 3239, 4.4),
    'de_inferno': MapConfig('de_inferno', 'INFERNO', -2087, 3870, 4.9),
    'de_ancient': MapConfig('de_ancient', 'ANCIENT', -2953, 2164, 5.0),
    'de_nuke': MapConfig('de_nuke', 'NUKE', -3453, 2887, 7.0),
    'de_overpass': MapConfig('de_overpass', 'OVERPASS', -4831, 1781, 5.2),
    'de_anubis': MapConfig('de_anubis', 'ANUBIS', -2796, 3328, 5.22),
    'de_vertigo': MapConfig('de_vertigo', 'VERTIGO', -3168, 1762, 4.0),
}


class Theme:
    """Premium glassmorphism-inspired color scheme."""
    # Dark backgrounds with depth
    BG = (6, 8, 12)
    BG_GRADIENT_TOP = (12, 16, 24)
    BG_GRADIENT_BOT = (4, 6, 10)
    PANEL = (12, 16, 22)
    PANEL_GLASS = (18, 24, 32, 200)  # Semi-transparent
    CARD = (22, 28, 38)
    CARD_HOVER = (32, 42, 58)
    CARD_ACTIVE = (40, 52, 70)
    BORDER = (45, 55, 75)
    BORDER_GLOW = (60, 80, 120)
    
    # Team colors - vibrant neon
    CT = (60, 140, 255)
    CT_LIGHT = (100, 180, 255)
    CT_DARK = (30, 80, 180)
    CT_GLOW = (60, 140, 255, 100)
    T = (255, 170, 40)
    T_LIGHT = (255, 200, 90)
    T_DARK = (180, 110, 25)
    T_GLOW = (255, 170, 40, 100)
    
    # Accent colors - neon
    ACCENT = (0, 220, 255)
    ACCENT_GLOW = (0, 200, 255, 80)
    ACCENT2 = (180, 80, 255)
    ACCENT2_GLOW = (180, 80, 255, 80)
    SUCCESS = (50, 230, 120)
    SUCCESS_GLOW = (50, 230, 120, 80)
    WARNING = (255, 180, 40)
    DANGER = (255, 60, 80)
    DANGER_GLOW = (255, 60, 80, 100)
    
    # Text
    WHITE = (245, 248, 255)
    GRAY = (130, 145, 170)
    MUTED = (70, 80, 100)
    DIM = (45, 52, 68)
    
    # Utility effects
    SMOKE = (160, 175, 200)
    SMOKE_GLOW = (160, 175, 200, 60)
    FIRE = (255, 100, 30)
    FIRE_GLOW = (255, 80, 20, 120)
    FLASH = (255, 255, 200)
    FLASH_GLOW = (255, 255, 180, 150)
    HE = (255, 80, 80)
    BOMB_GLOW = (255, 40, 40)
    BOMB_PULSE = (255, 80, 60, 150)
    
    # Grades with distinct colors
    GRADE_S = (255, 215, 0)    # Gold
    GRADE_A = (100, 255, 150)  # Green
    GRADE_B = (80, 180, 255)   # Blue
    GRADE_C = (180, 180, 190)  # Silver
    GRADE_D = (255, 150, 80)   # Orange
    GRADE_F = (255, 70, 70)    # Red

# UI Helper functions
def get_grade_color(grade: str) -> tuple:
    """Get color for performance grade."""
    colors = {
        'S': Theme.GRADE_S,
        'A': Theme.GRADE_A,
        'B': Theme.GRADE_B,
        'C': Theme.GRADE_C,
        'D': Theme.GRADE_D,
        'F': Theme.GRADE_F,
    }
    return colors.get(grade, Theme.GRAY)


def draw_glow_circle(surface, color, center, radius, glow_radius=None):
    """Draw circle with glow effect."""
    if glow_radius is None:
        glow_radius = radius + 8
    
    # Create glow surface
    glow_size = int(glow_radius * 2 + 4)
    glow_surf = pygame.Surface((glow_size, glow_size), pygame.SRCALPHA)
    
    # Draw multiple rings for glow
    for i in range(3):
        alpha = 40 - i * 12
        r = radius + (glow_radius - radius) * (i + 1) / 3
        pygame.draw.circle(glow_surf, (*color[:3], alpha), (glow_size//2, glow_size//2), int(r))
    
    # Blit glow
    surface.blit(glow_surf, (center[0] - glow_size//2, center[1] - glow_size//2))
    
    # Draw main circle
    pygame.draw.circle(surface, color, center, radius)


def draw_rounded_rect(surface, color, rect, radius=8, border=0, border_color=None):
    """Draw rounded rectangle with optional border."""
    pygame.draw.rect(surface, color, rect, border_radius=radius)
    if border > 0 and border_color:
        pygame.draw.rect(surface, border_color, rect, width=border, border_radius=radius)


class RadarReplayer:
    """Ultimate CS2 radar replay viewer with premium UI."""
    
    def __init__(self, width: int = 1600, height: int = 1000):
        pygame.init()
        pygame.font.init()
        
        self.width = width
        self.height = height
        self.radar_size = 640
        
        self.screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
        pygame.display.set_caption("SACRILEGE RADAR - Premium CS2 Demo Viewer")
        
        self.clock = pygame.time.Clock()
        self.frame = 0
        self.fps = 0
        self.fps_timer = 0
        self.anim_time = 0  # Animation timer
        
        # Fonts - HIGH VISIBILITY sizes
        try:
            self.font_xl = pygame.font.SysFont('SF Pro Display', 38, bold=True)
            self.font_lg = pygame.font.SysFont('SF Pro Display', 22, bold=True)
            self.font_md = pygame.font.SysFont('SF Pro Text', 17)
            self.font_sm = pygame.font.SysFont('SF Pro Text', 15)
            self.font_xs = pygame.font.SysFont('SF Pro Text', 13)
        except Exception:
            self.font_xl = pygame.font.SysFont('Arial', 38, bold=True)
            self.font_lg = pygame.font.SysFont('Arial', 22, bold=True)
            self.font_md = pygame.font.SysFont('Arial', 17)
            self.font_sm = pygame.font.SysFont('Arial', 15)
            self.font_xs = pygame.font.SysFont('Arial', 13)
        
        # UI State
        self.show_help = False
        self.hover_player = None
        self.selected_player = None
        
        # State
        self.demo_data = None
        self.map_config = None
        self.map_image = None
        self.map_scaled = None
        
        self.is_playing = False
        self.speed = 1.0
        
        # Data
        self.tick_df: Optional[pd.DataFrame] = None
        self.all_ticks: List[int] = []
        self.tick_idx = 0
        
        self.players: Dict[str, dict] = {}
        self.rounds: List[tuple] = []
        self.kills_by_tick: Dict[int, list] = {}
        self.current_round = 1
        
        # Utility
        self.smokes = []
        self.mollies = []
        self.flashes = []
        self.he_nades = []
        
        # Stats
        self.round_kills = {'CT': 0, 'T': 0}
        self.total_kills = {'CT': 0, 'T': 0}
        self.recent_kills = []
        
        # Animations
        self.kill_animations = []  # [(x, y, start_tick, team)]
        self.damage_indicators = []
        
        # Bomb state
        self.bomb_planted = False
        self.bomb_position = None
        self.bomb_plant_tick = 0
        
        # Death Analyzer
        self.death_analyzer = None
        self.death_popups = []  # [(analysis, show_until_tick)]
        self.analyzed_kills = set()  # Track which kills we've analyzed
        self.show_round_summary = False
        self.round_summary_tick = 0
        
        # New Features v1.4
        self.show_heatmap = False  # M key toggle
        self.death_positions = []  # [(x, y, team, round)]
        self.kill_positions = []  # [(x, y, team, round)] - NEW
        self.bookmarks = []  # [(tick, reason, data)]
        self.selected_player = None  # Clicked player card
        self.player_detail_mode = False
        
        # Enhanced Heatmap v1.5
        self.heatmap_mode = 'all'  # 'all', 'kills', 'deaths', 'ct', 't'
        self.heatmap_round_filter = None  # None = all rounds, or specific round number
        self.heatmap_density_grid = None  # Cached density grid
        self.heatmap_dirty = True  # Recalculate density
        
        # Player Trails
        self.show_trails = False  # T key toggle
        self.player_trails = {}  # {player_id: [(x, y, tick), ...]}
        self.trail_length = 60  # Number of positions to keep
        
        # AI Coach Integration
        self.ai_coach = None
        self.ai_insights = []  # List of (type, text, expire_time)
        self.ai_last_analysis = 0  # Tick of last analysis
        
        # Interactive Chat System
        self.chat_active = False  # Whether chat input is focused
        self.chat_input = ""  # Current text being typed
        self.chat_messages = []  # List of {'role': 'user'|'coach', 'text': str, 'time': int}
        self.chat_scroll = 0  # Scroll offset for chat history
        self.chat_waiting = False  # Waiting for AI response
        self.chat_panel_visible = True  # Show/hide the chat panel
        self.chat_cursor_blink = 0  # Cursor blink timer
        
        # Demo file path tracking
        self.demo_path = None
        self.demo_filename = ""
        
        # File browser state
        self.file_browser_active = True  # Start with browser active (welcome screen)
        self.available_demos = []  # List of Path objects
        self.file_browser_scroll = 0
        self.file_browser_hover = -1  # Index of hovered item
        self._scan_demos()
    
    def _scan_demos(self):
        """Scan for available demo files."""
        demo_dir = Path(__file__).parent.parent / 'demo files'
        self.available_demos = []
        if demo_dir.exists():
            self.available_demos = sorted(demo_dir.glob('*.dem'), key=lambda p: p.name)
        print(f"Found {len(self.available_demos)} demo files")
    
    def open_file_dialog(self):
        """Toggle the in-app file browser."""
        self._scan_demos()  # Refresh list
        self.file_browser_active = True
        self.file_browser_scroll = 0
    
    def _select_demo(self, index: int):
        """Load a demo from the available demos list."""
        if 0 <= index < len(self.available_demos):
            path = self.available_demos[index]
            self._reset_state()
            self.file_browser_active = False
            self.load_demo(path)
    
    def _reset_state(self):
        """Reset all state for loading a new demo."""
        self.demo_data = None
        self.tick_df = None
        self.all_ticks = []
        self.tick_idx = 0
        self.players = {}
        self.rounds = []
        self.kills_by_tick = {}
        self.current_round = 1
        self.smokes = []
        self.mollies = []
        self.flashes = []
        self.he_nades = []
        self.round_kills = {'CT': 0, 'T': 0}
        self.total_kills = {'CT': 0, 'T': 0}
        self.recent_kills = []
        self.kill_animations = []
        self.damage_indicators = []
        self.bomb_planted = False
        self.bomb_position = None
        self.death_analyzer = None
        self.death_popups = []
        self.analyzed_kills = set()
        self.death_positions = []
        self.kill_positions = []
        self.player_trails = {}
        self.heatmap_dirty = True
        self.ai_insights = []
        self.chat_messages = []
        self.chat_input = ""
        self.chat_scroll = 0
        self.is_playing = False
        
        # Reset AI coach conversation
        if self.ai_coach:
            self.ai_coach.reset_conversation()
    
    def load_demo(self, demo_path: Path) -> bool:
        from demoparser2 import DemoParser as DP2
        from src.parser.demo_parser import DemoParser
        
        self.demo_path = demo_path
        self.demo_filename = demo_path.name
        print(f"Loading: {demo_path.name}")
        
        try:
            parser = DemoParser()
            result = parser.parse(demo_path)
            if not result.success:
                print(f"Parse error: {result.error}")
                return False
            
            self.demo_data = result.data
            map_name = self.demo_data.header.map_name
            self.map_config = MAP_CONFIGS.get(map_name, MAP_CONFIGS['de_mirage'])
            
            self._load_map(map_name)
            
            # Players
            for pid, p in self.demo_data.players.items():
                self.players[pid] = {'name': p.name, 'team': p.team.name, 'kills': 0, 'deaths': 0}
            
            # Rounds 
            for rd in self.demo_data.rounds:
                self.rounds.append((rd.start_tick, rd.end_tick, rd.round_number))
            
            # Extract kills directly from demoparser2 (more reliable)
            dp2_temp = DP2(str(demo_path))
            kill_events = dp2_temp.parse_event("player_death")
            
            # Build steamid to player info mapping
            steamid_to_info = {}
            for sid, info in self.players.items():
                steamid_to_info[int(sid)] = info
            
            for _, row in kill_events.iterrows():
                tick = int(row.get('tick', 0))
                attacker_sid = row.get('attacker_steamid', 0)
                victim_sid = row.get('user_steamid', 0)
                
                attacker_info = steamid_to_info.get(attacker_sid, {})
                victim_info = steamid_to_info.get(victim_sid, {})
                
                # Track player stats
                if attacker_sid in steamid_to_info:
                    steamid_to_info[attacker_sid]['kills'] = steamid_to_info[attacker_sid].get('kills', 0) + 1
                if victim_sid in steamid_to_info:
                    steamid_to_info[victim_sid]['deaths'] = steamid_to_info[victim_sid].get('deaths', 0) + 1
                
                if tick not in self.kills_by_tick:
                    self.kills_by_tick[tick] = []
                
                self.kills_by_tick[tick].append({
                    'attacker': row.get('attacker_name', '?'),
                    'victim': row.get('user_name', '?'),
                    'attacker_team': attacker_info.get('team', 'CT'),
                    'victim_team': victim_info.get('team', 'T'),
                    'weapon': str(row.get('weapon', 'unknown')),
                    'hs': bool(row.get('headshot', False)),
                    'tick': tick,
                    'attacker_pos': None,  # Will get from tick data
                    'victim_pos': None,
                })
            
            print(f"✓ Extracted {len(kill_events)} kills")
            
            # Tick data - get ALL ticks first, then sample for playback but keep kill ticks
            print("Extracting positions...")
            dp2 = DP2(str(demo_path))
            data = dp2.parse_ticks([
                "X", "Y", "Z", "yaw", "health", "is_alive", 
                "armor_value", "has_defuser", "has_bomb", 
                "current_equip_value", "active_weapon_name"
            ])
            
            # Get all kill ticks for accurate heatmap
            kill_ticks = set(self.kills_by_tick.keys())
            
            if isinstance(data, pd.DataFrame):
                ticks = sorted(data['tick'].unique())
                # Sample every 4th tick for playback, but INCLUDE all kill ticks
                sampled = set(ticks[::4])
                required_ticks = sampled | kill_ticks
                self.tick_df = data[data['tick'].isin(required_ticks)]
                self.all_ticks = sorted(self.tick_df['tick'].unique())
                
                # Keep full data for kill tick lookups (steamid -> position at each tick)
                self.full_tick_data = data
            
            # Utility
            print("Extracting utility...")
            self._extract_utility(dp2)
            
            # Bomb events
            self._extract_bomb_events(dp2)
            
            # Initialize Death Analyzer
            from src.intelligence.death_analyzer import DeathAnalyzer
            self.death_analyzer = DeathAnalyzer()
            print("✓ Death analyzer initialized")
            
            # Initialize AI Coach
            try:
                from src.intelligence.llm_client import LLMClient
                self.ai_coach = LLMClient()
                if self.ai_coach.available:
                    print("✓ AI Coach initialized (Qwen 2.5)")
                else:
                    print("⚠ AI Coach unavailable (Ollama not found)")
            except Exception as e:
                print(f"⚠ AI Coach init failed: {e}")
                self.ai_coach = None
            
            # Pre-populate kill/death positions using EXACT tick data and steamid matching
            # Build steamid to name mapping
            steamid_to_name = {}
            for sid, info in self.players.items():
                steamid_to_name[sid] = info.get('name', '')
            
            # Process each kill event with accurate position data
            for tick, kills in self.kills_by_tick.items():
                # Find which round
                round_num = 1
                for s, e, n in self.rounds:
                    if s <= tick <= e:
                        round_num = n
                        break
                
                # Get position data at EXACT kill tick (or closest available)
                if hasattr(self, 'full_tick_data') and self.full_tick_data is not None:
                    # Find closest available tick
                    available = self.full_tick_data['tick'].unique()
                    closest_tick = min(available, key=lambda t: abs(t - tick))
                    tick_data = self.full_tick_data[self.full_tick_data['tick'] == closest_tick]
                    
                    # Build steamid -> position map for this tick
                    sid_to_pos = {}
                    for _, row in tick_data.iterrows():
                        sid = str(row.get('steamid', ''))
                        sid_to_pos[sid] = (float(row.get('X', 0)), float(row.get('Y', 0)))
                    
                    for k in kills:
                        # Get victim steamid from kill event
                        victim_name = k.get('victim', '')
                        attacker_name = k.get('attacker', '')
                        
                        # Find steamids by name (reverse lookup)
                        victim_sid = None
                        attacker_sid = None
                        for sid, name in steamid_to_name.items():
                            if name == victim_name:
                                victim_sid = sid
                            if name == attacker_name:
                                attacker_sid = sid
                        
                        # Look up victim position by steamid
                        if victim_sid and victim_sid in sid_to_pos:
                            pos = sid_to_pos[victim_sid]
                            if pos[0] != 0 or pos[1] != 0:  # Skip zero positions
                                self.death_positions.append({
                                    'x': pos[0],
                                    'y': pos[1],
                                    'team': k.get('victim_team', 'T'),
                                    'round': round_num,
                                    'victim': victim_name,
                                })
                        
                        # Look up attacker position by steamid
                        if attacker_sid and attacker_sid in sid_to_pos:
                            pos = sid_to_pos[attacker_sid]
                            if pos[0] != 0 or pos[1] != 0:  # Skip zero positions
                                self.kill_positions.append({
                                    'x': pos[0],
                                    'y': pos[1],
                                    'team': k.get('attacker_team', 'CT'),
                                    'round': round_num,
                                    'attacker': attacker_name,
                                })
            
            print(f"✓ Loaded: {len(self.all_ticks)} ticks, {len(self.rounds)} rounds")
            print(f"✓ Heatmap: {len(self.kill_positions)} kills, {len(self.death_positions)} deaths")
            print(f"✓ Utility: {len(self.smokes)} smokes, {len(self.mollies)} fires, {len(self.flashes)} flashes, {len(self.he_nades)} HEs")
            return True
            
        except Exception as e:
            print(f"Load error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _load_map(self, name):
        path = Path(__file__).parent / 'maps' / f"{name}.png"
        if path.exists():
            try:
                self.map_image = pygame.image.load(str(path))
                self.map_scaled = pygame.transform.smoothscale(self.map_image, (self.radar_size, self.radar_size))
                print(f"✓ Map loaded: {name}")
            except Exception as e:
                print(f"Map load failed: {e}")
    
    def _extract_utility(self, dp2):
        try:
            for _, r in dp2.parse_event("smokegrenade_detonate").iterrows():
                self.smokes.append({'x': r['x'], 'y': r['y'], 'start': r['tick'], 'end': r['tick'] + 1152})
        except: pass
        try:
            for _, r in dp2.parse_event("inferno_startburn").iterrows():
                self.mollies.append({'x': r['x'], 'y': r['y'], 'start': r['tick'], 'end': r['tick'] + 448})
        except: pass
        try:
            for _, r in dp2.parse_event("flashbang_detonate").iterrows():
                self.flashes.append({'x': r['x'], 'y': r['y'], 'start': r['tick'], 'end': r['tick'] + 40})
        except: pass
        try:
            for _, r in dp2.parse_event("hegrenade_detonate").iterrows():
                self.he_nades.append({'x': r['x'], 'y': r['y'], 'start': r['tick'], 'end': r['tick'] + 30})
        except: pass
    
    def _extract_bomb_events(self, dp2):
        try:
            plants = dp2.parse_event("bomb_planted")
            if isinstance(plants, pd.DataFrame) and len(plants) > 0:
                pass  # Would track bomb plants here
        except: pass
    
    def run(self):
        running = True
        last_tick_time = pygame.time.get_ticks()
        
        while running:
            self.frame += 1
            self.chat_cursor_blink += 1
            
            # FPS calculation
            now = pygame.time.get_ticks()
            if now - self.fps_timer > 1000:
                self.fps = self.clock.get_fps()
                self.fps_timer = now
            
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN:
                    if self.chat_active:
                        self._handle_chat_key(e)
                    else:
                        self._handle_key(e)
                elif e.type == pygame.TEXTINPUT:
                    if self.chat_active:
                        self.chat_input += e.text
                elif e.type == pygame.MOUSEBUTTONDOWN:
                    self._handle_click(e)
                elif e.type == pygame.MOUSEWHEEL:
                    if self.file_browser_active and not self.all_ticks:
                        max_scroll = max(0, len(self.available_demos) * 44 - (self.height - 240))
                        self.file_browser_scroll = max(0, min(max_scroll, self.file_browser_scroll - e.y * 30))
                    elif self.chat_active:
                        self.chat_scroll = max(0, self.chat_scroll - e.y * 3)
                elif e.type == pygame.VIDEORESIZE:
                    self.width, self.height = e.w, e.h
                    self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
            
            # Playback
            if self.is_playing and self.all_ticks:
                if now - last_tick_time > 25 / self.speed:
                    self.tick_idx = min(self.tick_idx + 1, len(self.all_ticks) - 1)
                    last_tick_time = now
                    self._update()
            
            self._render()
            pygame.display.flip()
            self.clock.tick(60)
        
        pygame.quit()
    
    def _handle_key(self, e):
        if e.key == pygame.K_SPACE:
            self.is_playing = not self.is_playing
        elif e.key == pygame.K_LEFT:
            self.tick_idx = max(0, self.tick_idx - 60)
            self._update()
        elif e.key == pygame.K_RIGHT:
            self.tick_idx = min(len(self.all_ticks) - 1, self.tick_idx + 60)
            self._update()
        elif e.key == pygame.K_UP:
            self.speed = min(8.0, self.speed * 2)
        elif e.key == pygame.K_DOWN:
            self.speed = max(0.25, self.speed / 2)
        elif e.key == pygame.K_r:
            self._next_round()
        elif e.key == pygame.K_e:
            self._prev_round()
        elif e.key == pygame.K_HOME:
            self.tick_idx = 0
            self._update()
        elif e.key == pygame.K_END:
            self.tick_idx = len(self.all_ticks) - 1
            self._update()
        elif e.key == pygame.K_F12:
            self._take_screenshot()
        elif e.key == pygame.K_h:
            self.show_help = not getattr(self, 'show_help', False)
        elif e.key == pygame.K_f:
            pygame.display.toggle_fullscreen()
        elif e.key == pygame.K_j:
            self._export_json()
        elif e.key == pygame.K_m:
            if e.mod & pygame.KMOD_SHIFT:
                self._export_heatmap()
            else:
                self.show_heatmap = not self.show_heatmap
                self.heatmap_dirty = True
                print(f"Heatmap: {'ON' if self.show_heatmap else 'OFF'}")
        elif e.key == pygame.K_b:
            self._add_bookmark()
        elif e.key == pygame.K_c:
            self._call_ai_coach()
        elif e.key == pygame.K_t:
            self.show_trails = not self.show_trails
            print(f"Player trails: {'ON' if self.show_trails else 'OFF'}")
        elif e.key == pygame.K_n:
            if self.heatmap_round_filter is None:
                self.heatmap_round_filter = self.current_round
                print(f"Heatmap filter: Round {self.current_round} only")
            else:
                self.heatmap_round_filter = None
                print("Heatmap filter: All rounds")
            self.heatmap_dirty = True
        elif e.key == pygame.K_o:
            # Open file dialog
            self.open_file_dialog()
        elif e.key == pygame.K_RETURN or e.key == pygame.K_KP_ENTER:
            # Enter chat mode
            self.chat_active = True
        elif e.key == pygame.K_1:
            self.heatmap_mode = 'all'
            self.heatmap_dirty = True
            print("Heatmap mode: ALL events")
        elif e.key == pygame.K_2:
            self.heatmap_mode = 'kills'
            self.heatmap_dirty = True
            print("Heatmap mode: KILLS only")
        elif e.key == pygame.K_3:
            self.heatmap_mode = 'deaths'
            self.heatmap_dirty = True
            print("Heatmap mode: DEATHS only")
        elif e.key == pygame.K_4:
            self.heatmap_mode = 'ct'
            self.heatmap_dirty = True
            print("Heatmap mode: CT events")
        elif e.key == pygame.K_5:
            self.heatmap_mode = 't'
            self.heatmap_dirty = True
            print("Heatmap mode: T events")
    
    def _handle_chat_key(self, e):
        """Handle keyboard events when chat is active."""
        if e.key == pygame.K_ESCAPE:
            self.chat_active = False
        elif e.key == pygame.K_RETURN or e.key == pygame.K_KP_ENTER:
            if self.chat_input.strip():
                self._send_chat_message(self.chat_input.strip())
                self.chat_input = ""
        elif e.key == pygame.K_BACKSPACE:
            if e.mod & pygame.KMOD_META or e.mod & pygame.KMOD_CTRL:
                self.chat_input = ""
            else:
                self.chat_input = self.chat_input[:-1]
        elif e.key == pygame.K_v and (e.mod & pygame.KMOD_META or e.mod & pygame.KMOD_CTRL):
            # Paste from clipboard
            try:
                import subprocess
                result = subprocess.run(['pbpaste'], capture_output=True, text=True)
                if result.returncode == 0:
                    self.chat_input += result.stdout.strip()
            except:
                pass
        elif e.key == pygame.K_UP:
            self.chat_scroll = max(0, self.chat_scroll + 3)
        elif e.key == pygame.K_DOWN:
            self.chat_scroll = max(0, self.chat_scroll - 3)
        # Note: actual text input comes via TEXTINPUT events (handled in event loop)
    
    def _take_screenshot(self):
        """Save screenshot to Downloads folder."""
        from datetime import datetime
        downloads = Path.home() / "Downloads"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = downloads / f"sacrilege_screenshot_{timestamp}.png"
        pygame.image.save(self.screen, str(filename))
        print(f"✓ Screenshot saved: {filename}")
    
    def _export_json(self):
        """Export current analysis to JSON file."""
        import json
        from datetime import datetime
        
        if not self.death_analyzer:
            print("✗ No analysis data available")
            return
        
        downloads = Path.home() / "Downloads"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = downloads / f"sacrilege_analysis_{timestamp}.json"
        
        rankings = self.death_analyzer.get_rankings()
        
        data = {
            "demo": self.demo_path.name if hasattr(self, 'demo_path') else "unknown",
            "map": self.map_config.display_name if self.map_config else "unknown",
            "current_round": self.current_round,
            "total_rounds": len(self.rounds),
            "rankings": [
                {
                    "player": r.name,
                    "team": r.team,
                    "grade": r.rank_grade,
                    "kills": r.kills,
                    "deaths": r.deaths,
                    "avg_blame": round(r.avg_blame, 1),
                    "kd_ratio": round(r.kd_ratio, 2),
                }
                for r in rankings
            ],
            "statistics": {
                "ct_kills": self.total_kills.get('CT', 0),
                "t_kills": self.total_kills.get('T', 0),
            }
        }
        
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"✓ Analysis exported: {filename}")
    
    def _add_bookmark(self):
        """Bookmark current position for later review."""
        if not self.all_ticks:
            return
        
        tick = self.all_ticks[self.tick_idx]
        bookmark = {
            'tick': tick,
            'tick_idx': self.tick_idx,
            'round': self.current_round,
            'timestamp': tick / 64.0,  # Approximate seconds
        }
        self.bookmarks.append(bookmark)
        print(f"✓ Bookmark added: Round {self.current_round}, Tick {tick} (#{len(self.bookmarks)})")
    
    def _export_heatmap(self):
        """Export current heatmap view as PNG."""
        from datetime import datetime
        
        if not self.show_heatmap:
            print("✗ Enable heatmap first (press M)")
            return
        
        downloads = Path.home() / "Downloads"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = self.heatmap_mode
        round_filter = f"_r{self.heatmap_round_filter}" if self.heatmap_round_filter else ""
        filename = downloads / f"sacrilege_heatmap_{mode}{round_filter}_{timestamp}.png"
        
        # Create standalone heatmap image
        heatmap_size = 800
        heatmap_surface = pygame.Surface((heatmap_size, heatmap_size))
        heatmap_surface.fill(Theme.BG)
        
        # Draw map background if available
        if self.map_scaled:
            scaled_map = pygame.transform.smoothscale(self.map_scaled, (heatmap_size, heatmap_size))
            heatmap_surface.blit(scaled_map, (0, 0))
        
        # Draw heatmap overlay using similar logic
        scale = heatmap_size / 1024
        grid_size = 64
        points = []
        
        for d in self.death_positions:
            if self.heatmap_round_filter and d.get('round') != self.heatmap_round_filter:
                continue
            if self.heatmap_mode == 'kills':
                continue
            if self.heatmap_mode == 'ct' and d['team'] != 'CT':
                continue
            if self.heatmap_mode == 't' and d['team'] != 'T':
                continue
            points.append((d['x'], d['y'], d['team'], 'death'))
        
        for k in self.kill_positions:
            if self.heatmap_round_filter and k.get('round') != self.heatmap_round_filter:
                continue
            if self.heatmap_mode == 'deaths':
                continue
            if self.heatmap_mode == 'ct' and k['team'] != 'CT':
                continue
            if self.heatmap_mode == 't' and k['team'] != 'T':
                continue
            points.append((k['x'], k['y'], k['team'], 'kill'))
        
        if points and self.map_config:
            overlay = pygame.Surface((heatmap_size, heatmap_size), pygame.SRCALPHA)
            density = [[0.0] * grid_size for _ in range(grid_size)]
            cell_size = heatmap_size / grid_size
            sigma = 2.0
            
            for wx, wy, team, event_type in points:
                px, py = self.map_config.world_to_radar(wx, wy, 1024)
                gx = int(px * scale / cell_size)
                gy = int(py * scale / cell_size)
                
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        nx, ny = gx + dx, gy + dy
                        if 0 <= nx < grid_size and 0 <= ny < grid_size:
                            dist_sq = dx * dx + dy * dy
                            weight = math.exp(-dist_sq / (2 * sigma * sigma))
                            density[ny][nx] += weight
            
            max_density = max(max(row) for row in density)
            if max_density > 0:
                for gy in range(grid_size):
                    for gx in range(grid_size):
                        val = density[gy][gx] / max_density
                        if val > 0.05:
                            if val < 0.25:
                                r, g, b = 0, int(val * 4 * 255), 255
                            elif val < 0.5:
                                r, g, b = 0, 255, int((1 - (val - 0.25) * 4) * 255)
                            elif val < 0.75:
                                r, g, b = int((val - 0.5) * 4 * 255), 255, 0
                            else:
                                r, g, b = 255, int((1 - (val - 0.75) * 4) * 255), 0
                            
                            alpha = int(min(180, val * 220 + 40))
                            x = int(gx * cell_size)
                            y = int(gy * cell_size)
                            pygame.draw.rect(overlay, (r, g, b, alpha), 
                                           (x, y, int(cell_size) + 1, int(cell_size) + 1))
            
            heatmap_surface.blit(overlay, (0, 0))
            
            # Draw markers
            for wx, wy, team, event_type in points:
                px, py = self.map_config.world_to_radar(wx, wy, 1024)
                x, y = int(px * scale), int(py * scale)
                
                if event_type == 'kill':
                    pygame.draw.line(heatmap_surface, Theme.SUCCESS, (x - 4, y), (x + 4, y), 2)
                    pygame.draw.line(heatmap_surface, Theme.SUCCESS, (x, y - 4), (x, y + 4), 2)
                else:
                    color = Theme.CT if team == 'CT' else Theme.T
                    pygame.draw.line(heatmap_surface, color, (x - 4, y - 4), (x + 4, y + 4), 2)
                    pygame.draw.line(heatmap_surface, color, (x + 4, y - 4), (x - 4, y + 4), 2)
        
        # Add title
        title = f"SACRILEGE HEATMAP - {self.map_config.display_name if self.map_config else 'Unknown'}"
        title_surf = self.font_lg.render(title, True, Theme.WHITE)
        heatmap_surface.blit(title_surf, (10, 10))
        
        pygame.image.save(heatmap_surface, str(filename))
        print(f"✓ Heatmap exported: {filename}")
    
    def _update_player_trails(self, players, tick):
        """Update trail history for all players."""
        for p in players:
            pid = p['id']
            if p['alive']:
                if pid not in self.player_trails:
                    self.player_trails[pid] = []
                
                trail = self.player_trails[pid]
                trail.append((p['x'], p['y'], tick))
                
                # Trim old positions
                while len(trail) > self.trail_length:
                    trail.pop(0)
            else:
                # Clear trail when dead
                if pid in self.player_trails:
                    self.player_trails[pid] = []
    
    def _draw_player_trails(self, players, rx, ry):
        """Draw movement trails for all players."""
        if not self.map_config:
            return
        
        scale = self.radar_size / 1024
        
        for p in players:
            if not p['alive']:
                continue
            
            pid = p['id']
            if pid not in self.player_trails or len(self.player_trails[pid]) < 2:
                continue
            
            trail = self.player_trails[pid]
            color = Theme.CT if p['team'] == 'CT' else Theme.T
            
            # Draw trail with fading alpha
            for i in range(1, len(trail)):
                x1, y1, _ = trail[i - 1]
                x2, y2, _ = trail[i]
                
                px1, py1 = self.map_config.world_to_radar(x1, y1, 1024)
                px2, py2 = self.map_config.world_to_radar(x2, y2, 1024)
                
                sx1, sy1 = rx + int(px1 * scale), ry + int(py1 * scale)
                sx2, sy2 = rx + int(px2 * scale), ry + int(py2 * scale)
                
                # Fade based on position in trail
                alpha = int(50 + (i / len(trail)) * 150)
                
                # Draw line segment with glow
                trail_surf = pygame.Surface((abs(sx2 - sx1) + 10, abs(sy2 - sy1) + 10), pygame.SRCALPHA)
                offset_x = min(sx1, sx2) - 5
                offset_y = min(sy1, sy2) - 5
                
                pygame.draw.line(self.screen, (*color[:3], alpha), (sx1, sy1), (sx2, sy2), 2)
    
    def _next_round(self):
        if not self.all_ticks: return
        t = self.all_ticks[self.tick_idx]
        for s, e, n in self.rounds:
            if s > t:
                for i, tick in enumerate(self.all_ticks):
                    if tick >= s:
                        self.tick_idx = i
                        self._update()
                        return
    
    def _prev_round(self):
        if not self.all_ticks: return
        t = self.all_ticks[self.tick_idx]
        for s, e, n in reversed(self.rounds):
            if e < t:
                for i, tick in enumerate(self.all_ticks):
                    if tick >= s:
                        self.tick_idx = i
                        self._update()
                        return
    
    def _handle_click(self, e):
        x, y = e.pos
        
        # Welcome screen / file browser - handle demo selection
        if not self.all_ticks or self.file_browser_active:
            cx = self.width // 2
            list_x = cx - 280
            list_w = 560
            list_y_start = 180  # header_y(140) + 40
            item_h = 44
            
            # Check if click is on a demo file item
            for i, demo_path in enumerate(self.available_demos):
                item_y = list_y_start + i * item_h - self.file_browser_scroll
                if list_x <= x <= list_x + list_w and item_y <= y <= item_y + item_h - 4:
                    if item_y >= list_y_start - 10 and item_y + item_h <= self.height - 40:
                        self._select_demo(i)
                        return
            return
        
        # Chat input area click - activate chat
        chat_px = self.width - 345
        chat_py = 685
        chat_pw = 330
        chat_ph = self.height - chat_py - 35
        if chat_px <= x <= chat_px + chat_pw and chat_py <= y <= chat_py + chat_ph:
            # Clicking anywhere in chat panel activates it
            self.chat_active = True
            return
        else:
            if self.chat_active:
                self.chat_active = False
                pass  # Just deactivate the flag
        
        # Player card click (left sidebar)
        if x < 360:
            # CT section (approx y = 85 to 320)
            if 85 <= y <= 320:
                card_idx = (y - 85) // 47
                ct_players = [n for n, p in self.players.items() if p.get('team') == 'CT']
                if 0 <= card_idx < len(ct_players):
                    self.selected_player = ct_players[card_idx]
                    print(f"Selected: {self.selected_player}")
                    return
            # T section (approx y = 360 to 580)
            elif 360 <= y <= 580:
                card_idx = (y - 360) // 47
                t_players = [n for n, p in self.players.items() if p.get('team') == 'T']
                if 0 <= card_idx < len(t_players):
                    self.selected_player = t_players[card_idx]
                    print(f"Selected: {self.selected_player}")
                    return
        
        # Timeline click
        ty = self.height - 42
        tx, tw = 380, self.width - 420
        if ty - 10 <= y <= ty + 30 and tx <= x <= tx + tw and self.all_ticks:
            self.tick_idx = int((x - tx) / tw * (len(self.all_ticks) - 1))
            self._update()
        
        # Clear selection on radar click
        if 375 <= x <= 375 + self.radar_size and 115 <= y <= 115 + self.radar_size:
            self.selected_player = None
    
    def _update(self):
        if not self.all_ticks: return
        tick = self.all_ticks[self.tick_idx]
        
        # Current round
        prev_round = self.current_round
        for s, e, n in self.rounds:
            if s <= tick <= e:
                self.current_round = n
                break
        
        # Reset on round change
        if self.current_round != prev_round:
            self.recent_kills.clear()
            self.kill_animations.clear()
            self.analyzed_kills.clear()
            if self.death_analyzer:
                self.death_analyzer.reset_round()
        
        # Kill feed and death analysis
        round_start = next((s for s, e, n in self.rounds if n == self.current_round), 0)
        
        # Recalculate round kills fresh each update (for seeking support)
        self.round_kills = {'CT': 0, 'T': 0}
        
        # Process kills in this round up to current tick
        for kt, kills in self.kills_by_tick.items():
            if round_start <= kt <= tick:
                for k in kills:
                    # Count for display
                    team = k.get('attacker_team', 'T')
                    self.round_kills[team] = self.round_kills.get(team, 0) + 1
                    
                    kill_id = f"{kt}_{k['victim']}"
                    
                    # Only analyze each kill once
                    if kill_id not in self.analyzed_kills:
                        self.analyzed_kills.add(kill_id)
                        
                        # Add to recent kills for display
                        self.recent_kills.append(k)
                        
                        # Add kill animation
                        if k.get('victim_pos'):
                            self.kill_animations.append({
                                'x': k['victim_pos'].x,
                                'y': k['victim_pos'].y,
                                'tick': kt,
                                'hs': k['hs'],
                            })
                        
                        # DEATH ANALYSIS
                        if self.death_analyzer:
                            # Track kill for rankings
                            self.death_analyzer.update_kill(k['attacker'], k['attacker_team'])
                            
                            # Get players at tick BEFORE death
                            pre_kill_tick = max(0, kt - 8)
                            players = self._get_players_for_analysis(pre_kill_tick, k['victim'])
                            
                            # Find victim's team
                            victim_team = 'T'
                            for p in players:
                                if p['name'] == k['victim']:
                                    victim_team = p['team']
                                    break
                            k['victim_team'] = victim_team
                            k['victim_id'] = ''
                            
                            # Analyze the death
                            analysis = self.death_analyzer.analyze_death(
                                k, players, self.smokes, self.mollies, 
                                self.flashes, self.recent_kills, kt, self.current_round
                            )
                            
                            # Add popup (show for 5 seconds = 320 ticks)
                            self.death_popups.append((analysis, kt + 320))
                            
                            # Trigger AI analysis (rate limited)
                            if self.ai_coach and kt - self.ai_last_analysis > 128:  # ~2 seconds
                                self.ai_last_analysis = kt
                                self._trigger_ai_analysis(analysis)
                            
                            # Track death position for heatmap
                            # Get victim's position from players list
                            victim_pos = None
                            for p in players:
                                if p['name'] == k['victim']:
                                    victim_pos = (p.get('x', 0), p.get('y', 0))
                                    break
                            
                            if victim_pos:
                                self.death_positions.append({
                                    'x': victim_pos[0],
                                    'y': victim_pos[1],
                                    'team': k['victim_team'],
                                    'round': self.current_round,
                                    'victim': k['victim'],
                                })
                                self.heatmap_dirty = True
                                
                                # Also track kill position for attacker
                                attacker_pos = k.get('attacker_pos')
                                if attacker_pos:
                                    self.kill_positions.append({
                                        'x': attacker_pos.x,
                                        'y': attacker_pos.y,
                                        'team': k['attacker_team'],
                                        'round': self.current_round,
                                        'attacker': k['attacker'],
                                    })
        
        # Trim old kills from feed
        self.recent_kills = [k for k in self.recent_kills if tick - k['tick'] < 400]
        
        # Clean old animations and popups
        self.kill_animations = [a for a in self.kill_animations if tick - a['tick'] < 64]
        self.death_popups = [(a, t) for a, t in self.death_popups if t > tick]
    
    def _get_players(self, tick):
        if self.tick_df is None: return []
        
        data = self.tick_df[self.tick_df['tick'] == tick]
        players = []
        
        for _, row in data.iterrows():
            sid = str(row.get('steamid', ''))
            if sid not in self.players: continue
            
            info = self.players[sid]
            alive = bool(row.get('is_alive', True))
            hp = int(row.get('health', 0))
            if hp > 0: alive = True
            
            players.append({
                'id': sid,
                'name': info['name'],
                'team': info['team'],
                'kills': info.get('kills', 0),
                'deaths': info.get('deaths', 0),
                'x': float(row.get('X', 0)),
                'y': float(row.get('Y', 0)),
                'yaw': float(row.get('yaw', 0)),
                'hp': hp if alive else 0,
                'armor': int(row.get('armor_value', 0)),
                'alive': alive,
                'bomb': bool(row.get('has_bomb', False)),
                'defuser': bool(row.get('has_defuser', False)),
                'equip': int(row.get('current_equip_value', 0)),
                'weapon': str(row.get('active_weapon_name', ''))[:12],
            })
        
        return players
    
    def _get_players_for_analysis(self, target_tick: int, victim_name: str):
        """Get players at closest available tick for death analysis."""
        if self.tick_df is None:
            return []
        
        # Find nearest tick in our sampled data
        available_ticks = self.tick_df['tick'].unique()
        nearest_tick = min(available_ticks, key=lambda t: abs(t - target_tick))
        
        data = self.tick_df[self.tick_df['tick'] == nearest_tick]
        players = []
        
        for _, row in data.iterrows():
            sid = str(row.get('steamid', ''))
            if sid not in self.players:
                continue
            
            info = self.players[sid]
            hp = int(row.get('health', 0))
            alive = hp > 0 or bool(row.get('is_alive', False))
            
            # The victim was alive before this death
            if info['name'] == victim_name:
                alive = True
                hp = max(hp, 1)
            
            players.append({
                'id': sid,
                'name': info['name'],
                'team': info['team'],
                'x': float(row.get('X', 0)),
                'y': float(row.get('Y', 0)),
                'alive': alive,
                'hp': hp,
            })
        
        return players
    
    def _render(self):
        self.screen.fill(Theme.BG)
        
        if not self.all_ticks:
            self._render_welcome()
            return
        
        tick = self.all_ticks[self.tick_idx]
        players = self._get_players(tick)
        
        self._draw_header(tick)
        self._draw_scoreboard(players)
        self._draw_player_list(players)
        self._draw_radar(players, tick)
        self._draw_killfeed()
        self._draw_death_panel()
        self._draw_round_stats(players)
        self._draw_timeline(tick)
        self._draw_legend()
        
        self._draw_death_popups(tick)
        self._draw_chat_panel()  # Interactive chat panel
        self._poll_ai_responses()
    
    def _render_welcome(self):
        """Render the welcome screen with demo file browser."""
        cx, cy = self.width // 2, self.height // 2
        
        # Animated background gradient
        for i in range(self.height):
            progress = i / self.height
            r = int(6 + progress * 8)
            g = int(8 + progress * 12)
            b = int(12 + progress * 20)
            pygame.draw.line(self.screen, (r, g, b), (0, i), (self.width, i))
        
        # Logo with animated glow
        pulse = 0.6 + abs(math.sin(self.frame * 0.025)) * 0.4
        glow_color = (0, int(200 * pulse), int(255 * pulse))
        
        # Large title
        title = self.font_xl.render("SACRILEGE", True, glow_color)
        self.screen.blit(title, (cx - title.get_width() // 2, 40))
        
        # Subtitle
        sub = self.font_lg.render("CS2 DEMO INTELLIGENCE VIEWER", True, Theme.GRAY)
        self.screen.blit(sub, (cx - sub.get_width() // 2, 85))
        
        # Separator line with gradient
        line_w = 400
        sep_y = 120
        for i in range(line_w):
            progress = 1.0 - abs(i - line_w/2) / (line_w/2)
            alpha_color = (int(60 * progress), int(140 * progress), int(255 * progress))
            pygame.draw.line(self.screen, alpha_color, (cx - line_w//2 + i, sep_y), (cx - line_w//2 + i, sep_y + 1))
        
        # Section header - SELECT DEMO
        header_y = 140
        header = self.font_lg.render(f"SELECT DEMO ({len(self.available_demos)} files)", True, Theme.ACCENT)
        self.screen.blit(header, (cx - header.get_width() // 2, header_y))
        
        # Demo file list
        list_x = cx - 280
        list_w = 560
        list_y_start = header_y + 40
        item_h = 44
        mx, my = pygame.mouse.get_pos()
        
        # Clip region for scrollable list
        max_visible_h = self.height - list_y_start - 60
        clip_rect = pygame.Rect(list_x - 5, list_y_start, list_w + 10, max_visible_h)
        self.screen.set_clip(clip_rect)
        
        if self.available_demos:
            for i, demo_path in enumerate(self.available_demos):
                item_y = list_y_start + i * item_h - self.file_browser_scroll
                
                # Skip items outside visible area
                if item_y + item_h < list_y_start or item_y > list_y_start + max_visible_h:
                    continue
                
                # Hover detection
                hovering = list_x <= mx <= list_x + list_w and item_y <= my <= item_y + item_h - 4
                
                # Card background
                if hovering:
                    bg_color = Theme.CARD_HOVER
                    # Glow effect
                    glow_surf = pygame.Surface((list_w + 8, item_h), pygame.SRCALPHA)
                    pygame.draw.rect(glow_surf, (0, 140, 255, 30), (0, 0, list_w + 8, item_h), border_radius=8)
                    self.screen.blit(glow_surf, (list_x - 4, item_y))
                else:
                    bg_color = Theme.CARD
                
                pygame.draw.rect(self.screen, bg_color, (list_x, item_y, list_w, item_h - 4), border_radius=8)
                
                # Accent bar
                accent = Theme.ACCENT if hovering else Theme.BORDER
                pygame.draw.rect(self.screen, accent, (list_x, item_y + 6, 4, item_h - 16), border_radius=2)
                
                # Index number
                idx_txt = self.font_sm.render(f"{i+1:02d}", True, Theme.MUTED)
                self.screen.blit(idx_txt, (list_x + 14, item_y + 12))
                
                # Demo filename (without extension)
                name = demo_path.stem
                name_color = Theme.WHITE if hovering else Theme.GRAY
                name_txt = self.font_md.render(name, True, name_color)
                self.screen.blit(name_txt, (list_x + 50, item_y + 8))
                
                # File size
                try:
                    size_mb = demo_path.stat().st_size / (1024 * 1024)
                    size_txt = self.font_xs.render(f"{size_mb:.0f} MB", True, Theme.MUTED)
                    self.screen.blit(size_txt, (list_x + list_w - 60, item_y + 14))
                except:
                    pass
                
                # Map name hint from filename
                for map_key in ['dust2', 'mirage', 'inferno', 'nuke', 'ancient', 'overpass', 'anubis', 'vertigo', 'train']:
                    if map_key in name.lower():
                        map_color = Theme.CT if hovering else Theme.DIM
                        map_txt = self.font_xs.render(map_key.upper(), True, map_color)
                        self.screen.blit(map_txt, (list_x + 50, item_y + 26))
                        break
        else:
            # No demos found
            no_demos = self.font_md.render("No .dem files found in 'demo files/' directory", True, Theme.MUTED)
            self.screen.blit(no_demos, (cx - no_demos.get_width() // 2, cy))
        
        # Remove clip
        self.screen.set_clip(None)
        
        # Scroll indicator
        total_h = len(self.available_demos) * item_h
        if total_h > max_visible_h:
            scroll_ratio = self.file_browser_scroll / max(1, total_h - max_visible_h)
            bar_h = max(30, int(max_visible_h * (max_visible_h / total_h)))
            bar_y = list_y_start + int((max_visible_h - bar_h) * scroll_ratio)
            pygame.draw.rect(self.screen, Theme.BORDER, (list_x + list_w + 4, bar_y, 4, bar_h), border_radius=2)
        
        # Bottom hint
        hint = self.font_sm.render("Click a demo to load  •  Scroll to browse  •  Press O to refresh", True, Theme.MUTED)
        self.screen.blit(hint, (cx - hint.get_width() // 2, self.height - 50))
        
        # Version info
        ver = self.font_xs.render("Sacrilege Engine v2.0  •  Powered by demoparser2 + Ollama", True, Theme.DIM)
        self.screen.blit(ver, (cx - ver.get_width() // 2, self.height - 28))
    
    def _draw_header(self, tick):
        # Header bar
        pygame.draw.rect(self.screen, Theme.PANEL, (0, 0, self.width, 52))
        
        # Animated logo
        pulse = 0.5 + abs(math.sin(self.frame * 0.03)) * 0.5
        glow = (0, min(255, int(180 * pulse + 75)), 255)
        logo = self.font_xl.render("SACRILEGE", True, glow)
        self.screen.blit(logo, (15, 10))
        
        # Demo filename
        if self.demo_filename:
            demo_txt = self.font_sm.render(self.demo_filename, True, Theme.GRAY)
            self.screen.blit(demo_txt, (18, 38))
        else:
            sub = self.font_xs.render("CS2 DEMO VIEWER", True, Theme.GRAY)
            self.screen.blit(sub, (18, 38))
        
        # Accent line with gradient effect
        for i in range(3):
            pygame.draw.line(self.screen, (*Theme.ACCENT[:3],), (0, 51 - i), (self.width, 51 - i), 1)
        
        # Playback info
        state = "▶ PLAYING" if self.is_playing else "⏸ PAUSED"
        state_color = Theme.SUCCESS if self.is_playing else Theme.WARNING
        self.screen.blit(self.font_md.render(state, True, state_color), (self.width - 180, 12))
        
        speed_txt = f"{self.speed}x"
        self.screen.blit(self.font_md.render(speed_txt, True, Theme.ACCENT), (self.width - 180, 30))
        
        # Chat mode indicator
        if self.chat_active:
            pygame.draw.rect(self.screen, Theme.ACCENT2, (self.width - 280, 14, 80, 20), border_radius=6)
            self.screen.blit(self.font_xs.render("CHAT ON", True, Theme.WHITE), (self.width - 270, 17))
        
        # FPS
        fps_txt = f"{int(self.fps)} FPS"
        self.screen.blit(self.font_xs.render(fps_txt, True, Theme.MUTED), (self.width - 60, 20))
    
    def _draw_scoreboard(self, players):
        cx = self.width // 2
        sy = 58
        
        ct_alive = sum(1 for p in players if p['team'] == 'CT' and p['alive'])
        t_alive = sum(1 for p in players if p['team'] == 'T' and p['alive'])
        
        # CT box
        pygame.draw.rect(self.screen, Theme.CT_DARK, (cx - 185, sy, 160, 48), border_radius=6)
        pygame.draw.rect(self.screen, Theme.CT, (cx - 185, sy, 4, 48), border_top_left_radius=6, border_bottom_left_radius=6)
        self.screen.blit(self.font_lg.render("CT", True, Theme.CT_LIGHT), (cx - 175, sy + 8))
        
        # CT alive indicator dots
        for i in range(5):
            color = Theme.CT if i < ct_alive else Theme.MUTED
            pygame.draw.circle(self.screen, color, (cx - 170 + i * 18, sy + 38), 5)
        
        # Center - Map and Round
        pygame.draw.rect(self.screen, Theme.CARD, (cx - 55, sy, 110, 48), border_radius=6)
        map_txt = self.map_config.display_name if self.map_config else "UNKNOWN"
        self.screen.blit(self.font_sm.render(map_txt, True, Theme.GRAY), (cx - 30, sy + 6))
        rd = self.font_lg.render(f"R{self.current_round}", True, Theme.WHITE)
        self.screen.blit(rd, (cx - rd.get_width()//2, sy + 24))
        
        # T box
        pygame.draw.rect(self.screen, Theme.T_DARK, (cx + 25, sy, 160, 48), border_radius=6)
        pygame.draw.rect(self.screen, Theme.T, (cx + 181, sy, 4, 48), border_top_right_radius=6, border_bottom_right_radius=6)
        self.screen.blit(self.font_lg.render("T", True, Theme.T_LIGHT), (cx + 155, sy + 8))
        
        # T alive indicator dots
        for i in range(5):
            color = Theme.T if i < t_alive else Theme.MUTED
            pygame.draw.circle(self.screen, color, (cx + 35 + i * 18, sy + 38), 5)
    
    def _draw_player_list(self, players):
        px, py = 20, 115
        pw = 350
        
        # CT Section
        pygame.draw.rect(self.screen, Theme.PANEL, (px, py, pw, 30), border_radius=5)
        pygame.draw.rect(self.screen, Theme.CT, (px, py, 4, 30), border_top_left_radius=5, border_bottom_left_radius=5)
        self.screen.blit(self.font_lg.render("COUNTER-TERRORISTS", True, Theme.CT), (px + 12, py + 6))
        
        cy = py + 34
        for p in sorted(players, key=lambda x: (-x['alive'], -x['hp'], x['name'])):
            if p['team'] == 'CT':
                self._draw_player_card(p, px, cy, pw)
                cy += 54
        
        # T Section
        ty = cy + 12
        pygame.draw.rect(self.screen, Theme.PANEL, (px, ty, pw, 30), border_radius=5)
        pygame.draw.rect(self.screen, Theme.T, (px, ty, 4, 30), border_top_left_radius=5, border_bottom_left_radius=5)
        self.screen.blit(self.font_lg.render("TERRORISTS", True, Theme.T), (px + 12, ty + 6))
        
        ty += 34
        for p in sorted(players, key=lambda x: (-x['alive'], -x['hp'], x['name'])):
            if p['team'] == 'T':
                self._draw_player_card(p, px, ty, pw)
                ty += 54
    
    def _draw_player_card(self, p, x, y, w):
        h = 50
        alive = p['alive']
        is_selected = p['name'] == self.selected_player
        
        # Background with selection/hover effect
        if is_selected:
            bg = Theme.CARD_ACTIVE
            # Glow border for selected
            pygame.draw.rect(self.screen, Theme.ACCENT, (x - 2, y - 2, w + 4, h + 4), 2, border_radius=8)
        else:
            bg = Theme.CARD if alive else (16, 18, 22)
        
        pygame.draw.rect(self.screen, bg, (x, y, w, h), border_radius=6)
        
        # Team accent bar
        color = Theme.CT if p['team'] == 'CT' else Theme.T
        if not alive:
            color = (color[0]//3, color[1]//3, color[2]//3)
        pygame.draw.rect(self.screen, color, (x, y + 6, 4, h - 12), border_radius=2)
        
        # Avatar with team color and glow for alive
        if alive:
            # Subtle glow
            glow_surf = pygame.Surface((40, 40), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*color[:3], 40), (20, 20), 18)
            self.screen.blit(glow_surf, (x + 6, y + h//2 - 20))
        
        pygame.draw.circle(self.screen, color, (x + 26, y + h//2), 14)
        pygame.draw.circle(self.screen, Theme.WHITE if alive else Theme.MUTED, (x + 26, y + h//2), 14, 2)
        
        # Bomb/Defuser indicators
        if p.get('bomb'):
            pulse = abs(math.sin(self.frame * 0.15)) * 5
            pygame.draw.circle(self.screen, Theme.BOMB_GLOW, (x + 26, y + h//2), int(6 + pulse))
            pygame.draw.circle(self.screen, Theme.DANGER, (x + 26, y + h//2), 5)
        if p.get('defuser'):
            pygame.draw.rect(self.screen, Theme.SUCCESS, (x + 36, y + 6, 10, 10), border_radius=2)
            self.screen.blit(self.font_xs.render("D", True, Theme.WHITE), (x + 39, y + 6))
        
        # Name
        name_color = Theme.WHITE if alive else Theme.MUTED
        self.screen.blit(self.font_md.render(p['name'], True, name_color), (x + 48, y + 6))
        
        # Player grade indicator (from death analyzer)
        if self.death_analyzer:
            rankings = self.death_analyzer.get_rankings()
            for r in rankings:
                if r.name == p['name']:
                    grade = r.rank_grade
                    grade_color = get_grade_color(grade)
                    # Draw grade badge
                    pygame.draw.rect(self.screen, grade_color, (x + w - 28, y + 4, 22, 18), border_radius=4)
                    self.screen.blit(self.font_sm.render(grade, True, Theme.BG), (x + w - 22, y + 5))
                    break
        
        if alive:
            # HP bar with gradient feel
            bar_x, bar_y, bar_w = x + 48, y + 28, 110
            pygame.draw.rect(self.screen, Theme.MUTED, (bar_x, bar_y, bar_w, 6), border_radius=3)
            
            hp = p['hp']
            hp_color = Theme.SUCCESS if hp > 50 else Theme.WARNING if hp > 25 else Theme.DANGER
            hp_w = int(bar_w * hp / 100)
            pygame.draw.rect(self.screen, hp_color, (bar_x, bar_y, hp_w, 6), border_radius=3)
            
            # Stats: HP | Armor | Equip value
            stats = f"{hp}HP  |  {p['armor']}  |  ${p['equip']}"
            self.screen.blit(self.font_xs.render(stats, True, Theme.GRAY), (x + 48, y + 38))
            
            # Weapon on right
            weapon = p.get('weapon', '').replace('weapon_', '')[:10]
            if weapon:
                w_txt = self.font_xs.render(weapon, True, Theme.MUTED)
                self.screen.blit(w_txt, (x + w - w_txt.get_width() - 10, y + 28))
            
            # K/D with color
            kills = p.get('kills', 0)
            deaths = p.get('deaths', 0)
            kd_color = Theme.SUCCESS if kills > deaths else Theme.DANGER if deaths > kills else Theme.GRAY
            self.screen.blit(self.font_sm.render(f"{kills}/{deaths}", True, kd_color), (x + w - 40, y + 40))
        else:
            self.screen.blit(self.font_sm.render("✕ ELIMINATED", True, Theme.DANGER), (x + 48, y + 28))
    
    def _draw_radar(self, players, tick):
        rx, ry = 400, 115
        
        # Panel background
        pygame.draw.rect(self.screen, Theme.PANEL, (rx - 10, ry - 10, self.radar_size + 20, self.radar_size + 20), border_radius=8)
        
        # Map image
        if self.map_scaled:
            self.screen.blit(self.map_scaled, (rx, ry))
        else:
            pygame.draw.rect(self.screen, Theme.CARD, (rx, ry, self.radar_size, self.radar_size))
            # Grid
            for i in range(0, self.radar_size, 50):
                pygame.draw.line(self.screen, Theme.BORDER, (rx + i, ry), (rx + i, ry + self.radar_size), 1)
                pygame.draw.line(self.screen, Theme.BORDER, (rx, ry + i), (rx + self.radar_size, ry + i), 1)
        
        # Utility
        self._draw_utility(rx, ry, tick)
        
        # Player trails (T key toggle)
        if self.show_trails:
            self._update_player_trails(players, tick)
            self._draw_player_trails(players, rx, ry)
        
        # Heatmap overlay (M key toggle)
        if self.show_heatmap:
            self._draw_heatmap_overlay(rx, ry)
        
        # Kill animations
        self._draw_kill_animations(rx, ry, tick)
        
        # Dead players
        for p in players:
            if not p['alive']:
                self._draw_dead_marker(p, rx, ry)
        
        # Alive players
        for p in players:
            if p['alive']:
                self._draw_player_dot(p, rx, ry)
        
        # Border
        pygame.draw.rect(self.screen, Theme.BORDER, (rx - 10, ry - 10, self.radar_size + 20, self.radar_size + 20), 2, border_radius=8)
    
    def _draw_utility(self, rx, ry, tick):
        scale = self.radar_size / 1024
        
        # Smokes with animated edges
        for s in self.smokes:
            if s['start'] <= tick <= s['end']:
                px, py = self.map_config.world_to_radar(s['x'], s['y'], 1024)
                x, y = rx + int(px * scale), ry + int(py * scale)
                
                # Animated smoke cloud
                offset = math.sin(self.frame * 0.08 + s['x'] * 0.01) * 3
                r = int(26 + offset)
                
                surf = pygame.Surface((r*2 + 16, r*2 + 16), pygame.SRCALPHA)
                pygame.draw.circle(surf, (*Theme.SMOKE, 130), (r + 8, r + 8), r)
                pygame.draw.circle(surf, (*Theme.SMOKE, 80), (r + 8, r + 8), r - 10)
                self.screen.blit(surf, (x - r - 8, y - r - 8))
        
        # Mollies with flickering
        for m in self.mollies:
            if m['start'] <= tick <= m['end']:
                px, py = self.map_config.world_to_radar(m['x'], m['y'], 1024)
                x, y = rx + int(px * scale), ry + int(py * scale)
                
                flicker = int(abs(math.sin(self.frame * 0.25 + m['x'] * 0.01)) * 50)
                surf = pygame.Surface((56, 56), pygame.SRCALPHA)
                pygame.draw.circle(surf, (255, 90 + flicker, 25, 170), (28, 28), 24)
                pygame.draw.circle(surf, (255, 180, 80, 100), (28, 28), 14)
                self.screen.blit(surf, (x - 28, y - 28))
        
        # Flashes with expanding ring
        for f in self.flashes:
            if f['start'] <= tick <= f['end']:
                px, py = self.map_config.world_to_radar(f['x'], f['y'], 1024)
                x, y = rx + int(px * scale), ry + int(py * scale)
                
                progress = (tick - f['start']) / 40
                r = int(15 + progress * 30)
                alpha = int(220 * (1 - progress))
                
                surf = pygame.Surface((r*2 + 16, r*2 + 16), pygame.SRCALPHA)
                pygame.draw.circle(surf, (*Theme.FLASH, alpha), (r + 8, r + 8), r)
                pygame.draw.circle(surf, (255, 255, 255, alpha//2), (r + 8, r + 8), r//2)
                self.screen.blit(surf, (x - r - 8, y - r - 8))
        
        # HE with explosion effect
        for h in self.he_nades:
            if h['start'] <= tick <= h['end']:
                px, py = self.map_config.world_to_radar(h['x'], h['y'], 1024)
                x, y = rx + int(px * scale), ry + int(py * scale)
                
                progress = (tick - h['start']) / 30
                r = int(20 + progress * 20)
                alpha = int(200 * (1 - progress))
                
                surf = pygame.Surface((r*2 + 16, r*2 + 16), pygame.SRCALPHA)
                pygame.draw.circle(surf, (*Theme.HE, alpha), (r + 8, r + 8), r)
                pygame.draw.circle(surf, (255, 200, 100, alpha), (r + 8, r + 8), int(r * 0.5))
                self.screen.blit(surf, (x - r - 8, y - r - 8))
    
    def _draw_kill_animations(self, rx, ry, tick):
        scale = self.radar_size / 1024
        
        for anim in self.kill_animations:
            px, py = self.map_config.world_to_radar(anim['x'], anim['y'], 1024)
            x, y = rx + int(px * scale), ry + int(py * scale)
            
            progress = (tick - anim['tick']) / 64
            r = int(20 + progress * 30)
            alpha = int(180 * (1 - progress))
            
            # Skull/death indicator
            color = (255, 80, 80, alpha) if anim['hs'] else (255, 150, 150, alpha)
            surf = pygame.Surface((r*2 + 10, r*2 + 10), pygame.SRCALPHA)
            pygame.draw.circle(surf, color, (r + 5, r + 5), 3)
            for i in range(4):
                angle = i * math.pi / 2
                lx = r + 5 + int(math.cos(angle) * (r * 0.7))
                ly = r + 5 + int(math.sin(angle) * (r * 0.7))
                pygame.draw.line(surf, color, (r + 5, r + 5), (lx, ly), 2)
            self.screen.blit(surf, (x - r - 5, y - r - 5))
    
    def _draw_player_dot(self, p, rx, ry):
        scale = self.radar_size / 1024
        px, py = self.map_config.world_to_radar(p['x'], p['y'], 1024)
        x, y = rx + int(px * scale), ry + int(py * scale)
        
        color = Theme.CT if p['team'] == 'CT' else Theme.T
        light = Theme.CT_LIGHT if p['team'] == 'CT' else Theme.T_LIGHT
        
        # Low HP warning pulse
        if p['hp'] < 30:
            pulse = abs(math.sin(self.frame * 0.12)) * 8
            pygame.draw.circle(self.screen, Theme.DANGER, (x, y), int(16 + pulse))
        
        # Outer glow
        pygame.draw.circle(self.screen, (*color, 100), (x, y), 14)
        
        # Main dot
        pygame.draw.circle(self.screen, color, (x, y), 10)
        pygame.draw.circle(self.screen, light, (x, y), 10, 2)
        
        # Bomb carrier - pulsing
        if p.get('bomb'):
            pulse = abs(math.sin(self.frame * 0.15)) * 4
            pygame.draw.circle(self.screen, Theme.BOMB_GLOW, (x, y), int(5 + pulse))
        
        # Defuser icon
        if p.get('defuser'):
            pygame.draw.circle(self.screen, Theme.SUCCESS, (x + 10, y - 10), 5)
        
        # Name label
        name = self.font_xs.render(p['name'][:7], True, Theme.WHITE)
        # Shadow
        shadow = self.font_xs.render(p['name'][:7], True, (0, 0, 0))
        self.screen.blit(shadow, (x - name.get_width()//2 + 1, y + 13))
        self.screen.blit(name, (x - name.get_width()//2, y + 12))
    
    def _draw_dead_marker(self, p, rx, ry):
        scale = self.radar_size / 1024
        px, py = self.map_config.world_to_radar(p['x'], p['y'], 1024)
        x, y = rx + int(px * scale), ry + int(py * scale)
        
        color = Theme.CT_DARK if p['team'] == 'CT' else Theme.T_DARK
        
        # X mark
        pygame.draw.line(self.screen, color, (x - 5, y - 5), (x + 5, y + 5), 2)
        pygame.draw.line(self.screen, color, (x + 5, y - 5), (x - 5, y + 5), 2)
    
    def _draw_killfeed(self):
        from src.intelligence.death_analyzer import DeathAnalyzer
        
        kx, ky = self.width - 345, 115
        kw = 330
        
        pygame.draw.rect(self.screen, Theme.PANEL, (kx - 5, ky - 5, kw + 10, 170), border_radius=6)
        self.screen.blit(self.font_md.render("KILL FEED", True, Theme.GRAY), (kx + 4, ky))
        
        ky += 22
        for i, k in enumerate(self.recent_kills[-4:]):
            y = ky + i * 36
            
            # Kill row background
            pygame.draw.rect(self.screen, (0, 0, 0, 120), (kx, y, kw, 32), border_radius=4)
            
            # Attacker
            kc = Theme.CT if k['attacker_team'] == 'CT' else Theme.T
            self.screen.blit(self.font_sm.render(k['attacker'][:10], True, kc), (kx + 6, y + 3))
            
            # Weapon + HS
            hs = " HS" if k['hs'] else ""
            weapon = f"[{k['weapon'][:8]}]{hs}"
            self.screen.blit(self.font_xs.render(weapon, True, Theme.MUTED), (kx + 110, y + 5))
            
            # Victim
            self.screen.blit(self.font_sm.render(k['victim'][:10], True, Theme.DANGER), (kx + 200, y + 3))
            
            # Death reason
            if self.death_analyzer:
                for analysis in self.death_analyzer.round_deaths:
                    if analysis.victim_name == k['victim']:
                        primary = analysis.primary_mistake()
                        label = DeathAnalyzer.get_mistake_label(primary)
                        color = DeathAnalyzer.get_mistake_color(primary)
                        self.screen.blit(self.font_xs.render(label, True, color), (kx + 6, y + 19))
                        break
    
    def _draw_round_stats(self, players):
        sx, sy = self.width - 345, 295
        sw, sh = 330, 130
        
        pygame.draw.rect(self.screen, Theme.PANEL, (sx - 5, sy, sw + 10, sh), border_radius=6)
        
        # Header
        self.screen.blit(self.font_lg.render("LIVE STATISTICS", True, Theme.ACCENT), (sx + 5, sy + 6))
        pygame.draw.line(self.screen, Theme.BORDER, (sx, sy + 28), (sx + sw, sy + 28), 1)
        
        cy = sy + 34
        
        # Round Kills - bigger numbers
        self.screen.blit(self.font_md.render("Round Kills", True, Theme.WHITE), (sx + 8, cy))
        ct_k = str(self.round_kills.get('CT', 0))
        t_k = str(self.round_kills.get('T', 0))
        self.screen.blit(self.font_xl.render(ct_k, True, Theme.CT), (sx + 160, cy - 6))
        self.screen.blit(self.font_lg.render(":", True, Theme.MUTED), (sx + 200, cy))
        self.screen.blit(self.font_xl.render(t_k, True, Theme.T), (sx + 220, cy - 6))
        cy += 30
        
        # Team HP
        ct_hp = sum(p['hp'] for p in players if p['team'] == 'CT' and p['alive'])
        t_hp = sum(p['hp'] for p in players if p['team'] == 'T' and p['alive'])
        self.screen.blit(self.font_md.render("Team HP", True, Theme.WHITE), (sx + 8, cy))
        self.screen.blit(self.font_lg.render(str(ct_hp), True, Theme.CT), (sx + 160, cy - 2))
        self.screen.blit(self.font_md.render(":", True, Theme.MUTED), (sx + 200, cy))
        self.screen.blit(self.font_lg.render(str(t_hp), True, Theme.T), (sx + 220, cy - 2))
        cy += 28
        
        # Equipment + Utility on same row
        ct_eq = sum(p['equip'] for p in players if p['team'] == 'CT' and p['alive'])
        t_eq = sum(p['equip'] for p in players if p['team'] == 'T' and p['alive'])
        self.screen.blit(self.font_md.render("Equip", True, Theme.WHITE), (sx + 8, cy))
        self.screen.blit(self.font_sm.render(f"${ct_eq}", True, Theme.CT), (sx + 100, cy + 2))
        self.screen.blit(self.font_sm.render(f"${t_eq}", True, Theme.T), (sx + 170, cy + 2))
        
        tick = self.all_ticks[self.tick_idx] if self.all_ticks else 0
        active_smokes = sum(1 for s in self.smokes if s['start'] <= tick <= s['end'])
        active_mollies = sum(1 for m in self.mollies if m['start'] <= tick <= m['end'])
        pygame.draw.circle(self.screen, Theme.SMOKE, (sx + 250, cy + 8), 6)
        self.screen.blit(self.font_sm.render(f"{active_smokes}", True, Theme.WHITE), (sx + 262, cy + 2))
        pygame.draw.circle(self.screen, Theme.FIRE, (sx + 290, cy + 8), 6)
        self.screen.blit(self.font_sm.render(f"{active_mollies}", True, Theme.WHITE), (sx + 302, cy + 2))
    
    def _draw_timeline(self, tick):
        ty = self.height - 48
        # Timeline ends before the right panel stack (1275)
        # 1275 - 380 = 895. Use 880 for safety.
        tx, tw = 380, 880
        
        # Background
        pygame.draw.rect(self.screen, Theme.PANEL, (tx - 18, ty - 10, tw + 36, 36), border_radius=8)
        pygame.draw.rect(self.screen, Theme.CARD, (tx, ty, tw, 18), border_radius=4)
        
        if self.all_ticks:
            total = len(self.all_ticks) - 1
            max_tick = self.all_ticks[-1] if self.all_ticks else 1
            
            if total > 0:
                prog = self.tick_idx / total
                pw = int(tw * prog)
                
                # Progress bar
                pygame.draw.rect(self.screen, Theme.ACCENT, (tx, ty, pw, 18), border_radius=4)
                
                # Kill markers on timeline
                for kill_tick, kills in self.kills_by_tick.items():
                    if kill_tick < max_tick:
                        kx = tx + int((kill_tick / max_tick) * tw)
                        for k in kills:
                            # CT deaths = blue dot, T deaths = orange dot
                            if k.get('victim_team') == 'CT' or 'CT' in str(k.get('victim', '')):
                                color = Theme.CT
                            else:
                                color = Theme.T
                            pygame.draw.circle(self.screen, color, (kx, ty + 9), 3)
                
                # Round markers
                for s, e, n in self.rounds:
                    if s < max_tick:
                        rx = tx + int((s / max_tick) * tw)
                        pygame.draw.line(self.screen, Theme.WHITE, (rx, ty - 5), (rx, ty + 23), 2)
                        # Round number
                        if n % 3 == 1:  # Show every 3rd round
                            self.screen.blit(self.font_xs.render(str(n), True, Theme.MUTED), (rx - 3, ty - 12))
                
                # Playhead
                pygame.draw.circle(self.screen, Theme.WHITE, (tx + pw, ty + 9), 10)
                pygame.draw.circle(self.screen, Theme.ACCENT, (tx + pw, ty + 9), 6)
                
                # Time
                secs = tick // 64
                mins, secs = secs // 60, secs % 60
                t_str = f"{mins}:{secs:02d}"
                self.screen.blit(self.font_md.render(t_str, True, Theme.WHITE), (tx - 55, ty - 1))
                
                # Duration
                total_secs = self.all_ticks[-1] // 64
                dur = f"{total_secs // 60}:{total_secs % 60:02d}"
                self.screen.blit(self.font_sm.render(dur, True, Theme.MUTED), (tx + tw + 8, ty + 1))
    
    def _draw_death_popups(self, tick):
        """Draw death analysis popups on the radar when deaths occur."""
        if not self.death_popups:
            return
        
        from src.intelligence.death_analyzer import DeathAnalyzer
        
        rx, ry = 375, 115
        scale = self.radar_size / 1024
        
        for analysis, expire_tick in self.death_popups:
            # Get position on radar
            px, py = self.map_config.world_to_radar(analysis.position[0], analysis.position[1], 1024)
            x, y = rx + int(px * scale), ry + int(py * scale)
            
            # Fade out effect
            time_left = expire_tick - tick
            alpha = min(255, int(time_left * 0.8))
            
            if analysis.mistakes:
                mistake = analysis.mistakes[0]
                color = DeathAnalyzer.get_mistake_color(mistake)
                
                # Draw larger popup box with full analytics
                box_w = 200
                box_h = 120
                box_x = min(x + 15, rx + self.radar_size - box_w - 10)
                box_y = max(y - box_h // 2, ry + 10)
                
                # Background
                surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
                pygame.draw.rect(surf, (20, 24, 32, min(230, alpha)), (0, 0, box_w, box_h), border_radius=8)
                pygame.draw.rect(surf, (*color, min(200, alpha)), (0, 0, 5, box_h), border_top_left_radius=8, border_bottom_left_radius=8)
                self.screen.blit(surf, (box_x, box_y))
                
                # Header - victim name + killer
                name_color = Theme.CT if analysis.victim_team == 'CT' else Theme.T
                self.screen.blit(self.font_md.render(f"{analysis.victim_name}", True, name_color), (box_x + 12, box_y + 6))
                self.screen.blit(self.font_xs.render(f"killed by {analysis.attacker_name}", True, Theme.MUTED), (box_x + 12, box_y + 24))
                
                # Severity badge
                sev_colors = [(100, 200, 100), (150, 200, 100), (255, 200, 50), (255, 120, 50), (255, 50, 50)]
                sev_color = sev_colors[min(analysis.severity - 1, 4)]
                pygame.draw.rect(self.screen, sev_color, (box_x + box_w - 28, box_y + 8, 22, 16), border_radius=4)
                self.screen.blit(self.font_sm.render(str(analysis.severity), True, (0, 0, 0)), (box_x + box_w - 21, box_y + 10))
                
                # Primary mistake label
                label = DeathAnalyzer.get_mistake_label(mistake)
                self.screen.blit(self.font_lg.render(label, True, color), (box_x + 12, box_y + 40))
                
                # Stats row 1: Teammates + Enemies
                cy = box_y + 65
                tm_dist = f"{int(analysis.teammate_distance)}u" if analysis.teammate_distance < 9000 else "ALONE"
                self.screen.blit(self.font_xs.render(f"Team: {tm_dist}", True, Theme.GRAY), (box_x + 12, cy))
                self.screen.blit(self.font_xs.render(f"vs {analysis.enemy_count} enemies", True, Theme.GRAY), (box_x + 110, cy))
                
                # Stats row 2: Trade + Blame
                cy += 16
                trade = "TRADED" if analysis.was_traded else "NOT TRADED"
                trade_color = Theme.SUCCESS if analysis.was_traded else Theme.DANGER
                self.screen.blit(self.font_xs.render(trade, True, trade_color), (box_x + 12, cy))
                
                blame = analysis.blame_score()
                # AI Coach Hint
                cy += 20
                if self.ai_coach and self.ai_coach.available:
                    pygame.draw.rect(self.screen, (100, 50, 255), (box_x + 10, cy + box_y - box_y, box_w - 20, 18), border_radius=4)
                    self.screen.blit(self.font_xs.render("Press C for AI Coach", True, Theme.WHITE), (box_x + 45, cy + 2))

                blame_color = (100, 200, 100) if blame < 40 else (255, 180, 50) if blame < 60 else (255, 80, 80)
                self.screen.blit(self.font_xs.render(f"Blame: {blame:.0f}%", True, blame_color), (box_x + 110, cy))
                
                # Additional mistakes
                cy += 16
                if len(analysis.mistakes) > 1:
                    other = ", ".join([DeathAnalyzer.get_mistake_label(m) for m in analysis.mistakes[1:3]])
                    self.screen.blit(self.font_xs.render(f"+{other}", True, Theme.MUTED), (box_x + 12, cy))
                
                # Line to death position
                pygame.draw.line(self.screen, (*color, min(150, alpha)), (x, y), (box_x, box_y + box_h // 2), 2)
    
    def _draw_death_panel(self):
        """Draw the Live Rankings panel."""
        if not self.death_analyzer:
            return
        
        from src.intelligence.death_analyzer import DeathAnalyzer
        
        # Panel position - below round stats (stats ends at 295+130=425)
        px = self.width - 345
        py = 435
        pw = 330
        ph = 240
        
        pygame.draw.rect(self.screen, Theme.PANEL, (px - 5, py, pw + 10, ph), border_radius=6)
        
        # === LIVE RANKINGS ===
        self.screen.blit(self.font_lg.render("LIVE RANKINGS", True, Theme.ACCENT), (px + 5, py + 8))
        pygame.draw.line(self.screen, Theme.BORDER, (px, py + 34), (px + pw, py + 34), 1)
        
        rankings = self.death_analyzer.get_rankings()
        cy = py + 42
        
        # Show top 8 players
        for i, stats in enumerate(rankings[:8]):
            # Rank number
            self.screen.blit(self.font_md.render(f"#{i+1}", True, Theme.MUTED), (px + 5, cy))
            
            # Grade badge
            grade = stats.rank_grade
            grade_color = DeathAnalyzer.get_grade_color(grade)
            pygame.draw.rect(self.screen, grade_color, (px + 35, cy + 1, 20, 16), border_radius=3)
            self.screen.blit(self.font_sm.render(grade, True, (0, 0, 0)), (px + 40, cy + 2))
            
            # Name
            name_color = Theme.CT if stats.team == 'CT' else Theme.T
            self.screen.blit(self.font_md.render(stats.name[:10], True, name_color), (px + 60, cy))
            
            # K/D
            kd = f"{stats.kills}/{stats.deaths}"
            self.screen.blit(self.font_md.render(kd, True, Theme.WHITE), (px + 160, cy))
            
            # Blame score
            blame = stats.avg_blame

            blame_color = (100, 200, 100) if blame < 40 else (255, 180, 50) if blame < 60 else (255, 80, 80)
            self.screen.blit(self.font_sm.render(f"{blame:.0f}%", True, blame_color), (px + 210, cy + 1))
            
            # Performance bar
            perf = min(100, stats.performance_score)
            bar_w = int(40 * perf / 100)
            pygame.draw.rect(self.screen, Theme.MUTED, (px + 250, cy + 5, 40, 8), border_radius=2)
            if bar_w > 0:
                pygame.draw.rect(self.screen, grade_color, (px + 250, cy + 5, bar_w, 8), border_radius=2)
            
            cy += 24

    def _call_ai_coach(self):
        """Trigger AI coaching for the latest or selected death."""
        if not self.ai_coach or not self.ai_coach.available:
            self.chat_messages.append({'role': 'coach', 'text': '⚠ AI Coach unavailable. Start Ollama to use chat.', 'time': self.frame})
            return
        
        # Get most recent death popup or selected player's last death
        target_analysis = None
        
        if self.death_popups:
            target_analysis = self.death_popups[-1][0]
            print(f"⚡ Asking Qwen to review death for {target_analysis.victim_name}...")
            prompt = self.death_analyzer.get_llm_prompt(target_analysis)
            
        elif self.selected_player and self.death_analyzer:
            stats = self.death_analyzer.player_stats.get(self.selected_player)
            if not stats:
                self.chat_messages.append({'role': 'coach', 'text': f'No stats found for that player yet.', 'time': self.frame})
                return
                
            print(f"⚡ Asking Qwen for deep analysis on {self.selected_player}...")
            prompt = self.death_analyzer.get_player_analysis_prompt(stats)
            
        else:
            # Activate chat mode instead
            self.chat_active = True
            return
        
        # Add user-visible message  
        self.chat_messages.append({'role': 'user', 'text': f'[Auto] Analyze: {prompt[:80]}...', 'time': self.frame})
        self.chat_waiting = True
        
        def on_response(text):
            print(f"⚡ Coach says: {text}")
            self.chat_messages.append({'role': 'coach', 'text': text, 'time': self.frame})
            self.chat_waiting = False
            # Also add to insights for on-screen display
            if self.all_ticks:
                expire = self.all_ticks[self.tick_idx] + 1280
                self.ai_insights.append(('coach', text, expire))
            
        self.ai_coach.generate_async(
            prompt, 
            on_response, 
            system_prompt=self.ai_coach.get_coach_persona()
        )
    
    def _send_chat_message(self, text: str):
        """Send a user chat message to the AI coach."""
        # Add user message to chat display
        self.chat_messages.append({'role': 'user', 'text': text, 'time': self.frame})
        self.chat_scroll = 0  # Scroll to bottom
        
        if not self.ai_coach or not self.ai_coach.available:
            self.chat_messages.append({
                'role': 'coach', 
                'text': '⚠ AI Coach unavailable. Start Ollama (ollama serve) and ensure qwen2.5 is pulled.',
                'time': self.frame
            })
            return
        
        self.chat_waiting = True
        
        # Build game context from current state
        context = self._build_chat_context()
        
        def on_response(response_text):
            self.chat_messages.append({
                'role': 'coach',
                'text': response_text,
                'time': self.frame
            })
            self.chat_waiting = False
            self.chat_scroll = 0
        
        # Use multi-turn chat
        self.ai_coach.chat_async(
            user_message=text,
            callback=on_response,
            context=context,
            system_prompt=self.ai_coach.get_chat_persona()
        )
    
    def _build_chat_context(self) -> str:
        """Build game context string from current demo state for the AI."""
        parts = []
        
        if self.map_config:
            parts.append(f"Map: {self.map_config.display_name}")
        parts.append(f"Round: {self.current_round}/{len(self.rounds)}")
        
        if self.demo_filename:
            parts.append(f"Demo: {self.demo_filename}")
        
        # Current round kills
        parts.append(f"Round Kills - CT: {self.round_kills.get('CT', 0)}, T: {self.round_kills.get('T', 0)}")
        
        # Player rankings
        if self.death_analyzer:
            rankings = self.death_analyzer.get_rankings()
            if rankings:
                parts.append("\nPlayer Rankings:")
                for i, r in enumerate(rankings[:10]):
                    parts.append(f"  #{i+1} [{r.rank_grade}] {r.name} ({r.team}) - K:{r.kills} D:{r.deaths} Blame:{r.avg_blame:.0f}%")
        
        # Recent deaths with analysis
        if self.death_analyzer and self.death_analyzer.round_deaths:
            parts.append("\nRecent Deaths This Round:")
            for d in self.death_analyzer.round_deaths[-5:]:
                primary = d.primary_mistake()
                from src.intelligence.death_analyzer import DeathAnalyzer
                label = DeathAnalyzer.get_mistake_label(primary)
                parts.append(f"  {d.victim_name} killed by {d.attacker_name} - {label} (Blame: {d.blame_score():.0f}%)")
        
        return "\n".join(parts)

    def _draw_heatmap_overlay(self, rx, ry):
        """Draw PRECISE heatmap - only exact event positions, no interpolation."""
        if not self.map_config:
            return
        
        scale = self.radar_size / 1024
        
        # Gather points based on filter mode
        death_points = []
        kill_points = []
        
        # Filter deaths
        if self.heatmap_mode in ('all', 'deaths', 'ct', 't'):
            for d in self.death_positions:
                if self.heatmap_round_filter and d.get('round') != self.heatmap_round_filter:
                    continue
                if self.heatmap_mode == 'ct' and d['team'] != 'CT':
                    continue
                if self.heatmap_mode == 't' and d['team'] != 'T':
                    continue
                if self.heatmap_mode != 'kills':
                    death_points.append((d['x'], d['y'], d['team'], d.get('victim', '')))
        
        # Filter kills
        if self.heatmap_mode in ('all', 'kills', 'ct', 't'):
            for k in self.kill_positions:
                if self.heatmap_round_filter and k.get('round') != self.heatmap_round_filter:
                    continue
                if self.heatmap_mode == 'ct' and k['team'] != 'CT':
                    continue
                if self.heatmap_mode == 't' and k['team'] != 'T':
                    continue
                if self.heatmap_mode != 'deaths':
                    kill_points.append((k['x'], k['y'], k['team'], k.get('attacker', '')))
        
        # Create overlay surface
        overlay = pygame.Surface((self.radar_size, self.radar_size), pygame.SRCALPHA)
        
        total_events = len(death_points) + len(kill_points)
        
        if total_events > 0:
            # Draw DEATH markers - X marks with outer glow
            for wx, wy, team, name in death_points:
                px, py = self.map_config.world_to_radar(wx, wy, 1024)
                x, y = int(px * scale), int(py * scale)
                
                # Team color
                if team == 'CT':
                    color = (100, 180, 255)  # CT blue
                    glow_color = (60, 120, 200, 80)
                else:
                    color = (255, 180, 80)  # T orange/gold
                    glow_color = (200, 120, 40, 80)
                
                # Outer glow ring
                pygame.draw.circle(overlay, glow_color, (x, y), 8)
                pygame.draw.circle(overlay, (*color, 150), (x, y), 5)
                
                # X mark
                pygame.draw.line(overlay, (*color, 255), (x - 4, y - 4), (x + 4, y + 4), 2)
                pygame.draw.line(overlay, (*color, 255), (x + 4, y - 4), (x - 4, y + 4), 2)
            
            # Draw KILL markers - Diamond with inner dot
            for wx, wy, team, name in kill_points:
                px, py = self.map_config.world_to_radar(wx, wy, 1024)
                x, y = int(px * scale), int(py * scale)
                
                # Green for kills
                color = (80, 255, 120)
                glow_color = (40, 180, 80, 100)
                
                # Outer glow
                pygame.draw.circle(overlay, glow_color, (x, y), 7)
                
                # Diamond shape
                pts = [(x, y - 5), (x + 5, y), (x, y + 5), (x - 5, y)]
                pygame.draw.polygon(overlay, (*color, 200), pts)
                pygame.draw.polygon(overlay, (255, 255, 255, 255), pts, 1)
                
                # Center dot
                pygame.draw.circle(overlay, (255, 255, 255, 255), (x, y), 2)
        else:
            # Empty state
            center_y = self.radar_size // 2
            text = self.font_md.render("No events to display", True, Theme.MUTED)
            overlay.blit(text, (self.radar_size // 2 - text.get_width() // 2, center_y - 10))
        
        # Blit overlay
        self.screen.blit(overlay, (rx, ry))

    def _draw_legend(self):
        ly = self.height - 20
        lx = 15
        
        # Utility legend - compact row
        items = [
            (Theme.SMOKE, "Smoke"),
            (Theme.FIRE, "Fire"),
            (Theme.FLASH, "Flash"),
            (Theme.HE, "HE"),
        ]
        
        for color, label in items:
            pygame.draw.circle(self.screen, color, (lx + 6, ly), 6)
            txt = self.font_sm.render(label, True, Theme.WHITE)
            self.screen.blit(txt, (lx + 16, ly - 8))
            lx += txt.get_width() + 28
        
        # Controls - only essential keys, readable
        controls = "SPACE Play  ←→ Seek  ↑↓ Speed  E/R Round  O Open  Enter Chat  M Heatmap  C Coach"
        self.screen.blit(self.font_xs.render(controls, True, Theme.MUTED), (lx + 20, ly - 7))
    
    def _draw_chat_panel(self):
        """Draw the interactive AI Coach chat panel with message history and input."""
        px = self.width - 345
        py = 685
        pw = 330
        ph = self.height - py - 35
        
        if ph < 80:
            return  # Too small to draw
        
        # Panel background with glow when active
        surf = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pygame.draw.rect(surf, (*Theme.PANEL[:3], 245), (0, 0, pw, ph), border_radius=8)
        
        border_color = Theme.ACCENT2 if self.chat_active else Theme.ACCENT
        border_w = 2 if not self.chat_active else 3
        pygame.draw.rect(surf, border_color, (0, 0, pw, ph), border_w, border_radius=8)
        self.screen.blit(surf, (px, py))
        
        # Header bar
        pygame.draw.rect(self.screen, Theme.CARD, (px + 2, py + 2, pw - 4, 34), border_top_left_radius=7, border_top_right_radius=7)
        self.screen.blit(self.font_lg.render("AI COACH", True, Theme.ACCENT), (px + 12, py + 8))
        
        # Model indicator + status dot
        if self.ai_coach and self.ai_coach.available:
            model = self.ai_coach.model.split(':')[0].upper()
            pygame.draw.circle(self.screen, Theme.SUCCESS, (px + pw - 14, py + 20), 5)
            self.screen.blit(self.font_xs.render(model, True, Theme.MUTED), (px + pw - 80, py + 14))
        else:
            pygame.draw.circle(self.screen, Theme.DANGER, (px + pw - 14, py + 20), 5)
            self.screen.blit(self.font_xs.render("OFFLINE", True, Theme.DANGER), (px + pw - 70, py + 14))
        
        # ── Input box (bottom, draw FIRST to get correct message area) ──
        input_h = 36
        input_y = py + ph - input_h - 6
        input_w = pw - 16
        
        input_bg = Theme.CARD_ACTIVE if self.chat_active else Theme.CARD
        pygame.draw.rect(self.screen, input_bg, (px + 8, input_y, input_w, input_h), border_radius=8)
        
        if self.chat_active:
            # Active glow border
            pygame.draw.rect(self.screen, Theme.ACCENT2, (px + 8, input_y, input_w, input_h), 2, border_radius=8)
            
            # Input text - scroll to show end of text
            display_text = self.chat_input
            max_text_w = input_w - 50  # Reserve space for send button
            while display_text and self.font_md.size(display_text)[0] > max_text_w:
                display_text = display_text[1:]  # Trim from left
            
            # Blinking cursor
            cursor = "|" if (self.chat_cursor_blink // 25) % 2 == 0 else " "
            txt = self.font_md.render(display_text + cursor, True, Theme.WHITE)
            self.screen.blit(txt, (px + 16, input_y + 9))
            
            # Send button when there's text
            if self.chat_input.strip():
                send_x = px + 8 + input_w - 32
                send_y = input_y + 4
                pygame.draw.rect(self.screen, Theme.ACCENT, (send_x, send_y, 28, 28), border_radius=6)
                pygame.draw.polygon(self.screen, Theme.WHITE, [
                    (send_x + 8, send_y + 20), (send_x + 14, send_y + 6), (send_x + 20, send_y + 20)
                ])
        else:
            placeholder = "Click or press Enter to chat..."
            txt = self.font_sm.render(placeholder, True, Theme.MUTED)
            self.screen.blit(txt, (px + 16, input_y + 10))
        
        # ── Chat messages area ──
        msg_y_start = py + 40
        msg_y_end = input_y - 6
        msg_area_h = msg_y_end - msg_y_start
        
        if msg_area_h < 20:
            return  # Too small
        
        clip_rect = pygame.Rect(px + 4, msg_y_start, pw - 8, msg_area_h)
        self.screen.set_clip(clip_rect)
        
        if self.chat_messages:
            y = msg_y_end - 4
            scroll_offset = self.chat_scroll * 20
            y += scroll_offset
            
            for msg in reversed(self.chat_messages):
                role = msg['role']
                text = msg['text']
                
                # Word wrap
                max_w = pw - 50
                wrapped_lines = []
                for paragraph in text.split('\n'):
                    if not paragraph.strip():
                        wrapped_lines.append("")
                        continue
                    words = paragraph.split()
                    current_line = ""
                    for word in words:
                        test = (current_line + " " + word) if current_line else word
                        if self.font_sm.size(test)[0] < max_w:
                            current_line = test
                        else:
                            if current_line:
                                wrapped_lines.append(current_line)
                            current_line = word
                    if current_line:
                        wrapped_lines.append(current_line)
                
                if not wrapped_lines:
                    wrapped_lines = [""]
                
                line_h = 18
                msg_padding = 10
                msg_h = len(wrapped_lines) * line_h + msg_padding * 2
                y -= msg_h + 4  # 4px gap between messages
                
                if y > msg_y_end + 80:
                    continue
                if y + msg_h < msg_y_start - 80:
                    break
                
                # Calculate bubble width from longest line
                max_line_w = max((self.font_sm.size(l)[0] for l in wrapped_lines if l), default=40)
                bubble_w = min(pw - 40, max_line_w + 20)
                
                if role == 'user':
                    # User bubble - right-aligned, blue
                    bubble_x = px + pw - bubble_w - 12
                    pygame.draw.rect(self.screen, (25, 55, 115), 
                                   (bubble_x, y, bubble_w, msg_h), border_radius=10)
                    pygame.draw.rect(self.screen, (40, 80, 160), 
                                   (bubble_x, y, bubble_w, msg_h), 1, border_radius=10)
                    
                    ly = y + msg_padding
                    for line in wrapped_lines:
                        if line:
                            self.screen.blit(self.font_sm.render(line, True, Theme.CT_LIGHT), 
                                           (bubble_x + 10, ly))
                        ly += line_h
                else:
                    # Coach bubble - left-aligned, dark with accent bar
                    bubble_x = px + 12
                    pygame.draw.rect(self.screen, (24, 28, 40), 
                                   (bubble_x, y, bubble_w, msg_h), border_radius=10)
                    pygame.draw.rect(self.screen, Theme.ACCENT2, 
                                   (bubble_x, y + 4, 3, msg_h - 8), border_radius=2)
                    
                    ly = y + msg_padding
                    for line in wrapped_lines:
                        if line:
                            self.screen.blit(self.font_sm.render(line, True, Theme.WHITE), 
                                           (bubble_x + 12, ly))
                        ly += line_h
        else:
            # Empty state
            center_x = px + pw // 2
            center_y = msg_y_start + msg_area_h // 2
            
            pulse = abs(math.sin(self.frame * 0.04)) * 50
            
            lines = [
                ("Type a question about the match", True),
                ("Press C anytime for instant death coaching", False),
                ("Ask about strategy, positioning, rotations", False),
            ]
            
            for i, (tip_text, highlight) in enumerate(lines):
                color = (int(140 + pulse), int(160 + pulse), int(200 + pulse)) if highlight else Theme.MUTED
                txt = self.font_sm.render(tip_text, True, color)
                self.screen.blit(txt, (center_x - txt.get_width() // 2, center_y - 30 + i * 24))
        
        self.screen.set_clip(None)
        
        # Waiting indicator (above input)
        if self.chat_waiting:
            dots = "." * ((self.frame // 12) % 4)
            wait = self.font_sm.render(f"Thinking{dots}", True, Theme.ACCENT2)
            self.screen.blit(wait, (px + 14, input_y - 20))
    
    def _poll_ai_responses(self):
        """Clean expired insights."""
        if self.ai_coach and self.all_ticks:
            tick = self.all_ticks[self.tick_idx]
            self.ai_insights = [(t, txt, exp) for t, txt, exp in self.ai_insights if exp > tick]
    
    def _trigger_ai_analysis(self, analysis):
        """Trigger AI analysis for a death."""
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description='SACRILEGE RADAR - CS2 Demo Viewer')
    parser.add_argument('demo', nargs='?', help='Demo file path (optional - use file dialog if omitted)')
    parser.add_argument('--no-welcome', action='store_true', help='Skip welcome screen, auto-load first demo')
    args = parser.parse_args()
    
    replayer = RadarReplayer(1500, 920)
    
    if args.demo:
        replayer.load_demo(Path(args.demo))
    elif args.no_welcome:
        demo_dir = Path(__file__).parent.parent / 'demo files'
        if demo_dir.exists():
            demos = list(demo_dir.glob('*.dem'))
            if demos:
                replayer.load_demo(demos[0])
    # else: show welcome screen with file dialog button
    
    replayer.run()


if __name__ == '__main__':
    main()

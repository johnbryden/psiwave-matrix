import moderngl
import numpy as np
from pyrr import Matrix44
from numba import jit
from engine.base_effect import Base3DEffect
from midi_input import apply_curve, get_latest_cc, get_latest_note, sigmoid, reverse_sigmoid
import time
import colorsys
from collections import deque
from random import randint

class RandomUniformCache:
    def __init__(self, cache_size=10000):
        self.cache_size = cache_size
        self.cache = np.random.uniform(0.0, 1.0, cache_size)
        self.index = 0

    def uniform(self, a, b):
        value = self.cache[self.index]
        self.index = (self.index + 1) % self.cache_size
        return a + (b - a) * value

@jit(nopython=True)
def calculate_rotation_matrix_jit(angle, axis_x, axis_y, axis_z):
    """Calculate rotation matrix using Numba for speed"""
    norm = np.sqrt(axis_x * axis_x + axis_y * axis_y + axis_z * axis_z)
    if norm > 0:
        x = axis_x / norm
        y = axis_y / norm
        z = axis_z / norm
    else:
        return np.identity(4, dtype=np.float32)

    cos_a = np.cos(angle)
    sin_a = np.sin(angle)
    one_minus_cos = 1.0 - cos_a

    matrix = np.identity(4, dtype=np.float32)
    matrix[0, 0] = cos_a + x * x * one_minus_cos
    matrix[0, 1] = x * y * one_minus_cos - z * sin_a
    matrix[0, 2] = x * z * one_minus_cos + y * sin_a
    matrix[1, 0] = y * x * one_minus_cos + z * sin_a
    matrix[1, 1] = cos_a + y * y * one_minus_cos
    matrix[1, 2] = y * z * one_minus_cos - x * sin_a
    matrix[2, 0] = z * x * one_minus_cos - y * sin_a
    matrix[2, 1] = z * y * one_minus_cos + x * sin_a
    matrix[2, 2] = cos_a + z * z * one_minus_cos

    return matrix

class StarfieldEffect(Base3DEffect):
    def __init__(self, context, config, num_stars=500, speed=10):
        self.init_midi_control_parameters(config)

        self.program = context.program(
            vertex_shader="""
                #version 330
                in vec3 in_vert;
                in vec4 in_color;
                in vec3 in_offset;
                out vec4 v_color;
                uniform mat4 projection;
                uniform mat4 view;
                uniform mat4 z_rotation;
                void main() {
                    vec4 rotated_pos = z_rotation * vec4(in_offset, 1.0);
                    mat4 model = mat4(1.0);
                    model[3].xyz = rotated_pos.xyz;
                    gl_Position = projection * view * model * vec4(in_vert, 1.0);
                    v_color = in_color;
                }
            """,
            fragment_shader="""
                #version 330
                in vec4 v_color;
                out vec4 f_color;
                void main() {
                    if (v_color.a == 0.0) discard;
                    f_color = v_color;
                }
            """
        )

        self.random_cache = RandomUniformCache()
        super().__init__(context)

        self.config = config
        self.num_stars = num_stars
        self.base_speed = speed
        self.rotation_speed = 0
        self.rotation_angle = 0
        self.last_time = time.time()

        self.num_groups = 12
        self.colour_mix_1 = (.3, .3, 1)
        self.colour_mix_2 = (.3, 1, .3)
        self.group_colors = np.array([self.generate_random_colour_from_hsv() for _ in range(self.num_groups)])

        self.stars = np.array([self.get_star_position() for _ in range(num_stars)], dtype='f4')
        self.star_groups = np.array([randint(0, self.num_groups - 1) for _ in range(num_stars)], dtype='i4')
        self.colors = np.array([self.group_colors[self.star_groups[i]] for i in range(num_stars)], dtype='f4')

        self.exploding_stars = deque()
        self.explosion_duration = 10.0
        self.num_star_fragments = 10

        self.create_star_geometry()

        self.vbo = self.context.buffer(self.vertices.tobytes())
        self.cbo = self.context.buffer(self.colors.tobytes())
        self.ibo = self.context.buffer(self.stars.tobytes())

        self.vao_content = [
            (self.vbo, '3f', 'in_vert'),
            (self.cbo, '4f/i', 'in_color'),
            (self.ibo, '3f/i', 'in_offset')
        ]

        self.star_vao = self.context.vertex_array(
            self.program, self.vao_content
        )

    def init_midi_control_parameters(self, config):
        self.starfield_rotation_cc = config["starfield_rotation"][1]
        self.starfield_rotation_channel = config["starfield_rotation"][0]
        self.starfield_speed_cc = config["starfield_speed"][1]
        self.starfield_speed_channel = config["starfield_speed"][0]

    def create_star_geometry(self):
        radius = 0.1
        angles = np.linspace(0, 2 * np.pi, 9)[:-1]
        vertices = [[0, 0, 0]] + [[radius * np.cos(a), radius * np.sin(a), 0] for a in angles] + [[radius, 0, 0]]
        self.vertices = np.array(vertices, dtype='f4')

    def get_star_position(self):
        scale = 5
        return (
            self.random_cache.uniform(-8, 8) * scale,
            self.random_cache.uniform(-6, 6) * scale,
            self.random_cache.uniform(-100, -20) * scale
        )

    def generate_random_colour_from_hsv(self):
        h, s, v = self.random_cache.uniform(0, 1), self.random_cache.uniform(0.8, 1.0), self.random_cache.uniform(0.8, 1.0)
        return (*colorsys.hsv_to_rgb(h, s, v), 1.0)

    def explode_star_group(self, group):
        indices = np.where(self.star_groups == group)[0]
        for idx in indices:
            if self.stars[idx][2] < -30:
                original_pos = self.stars[idx]
                for _ in range(self.num_star_fragments):
                    self.exploding_stars.append({
                        'position': np.array(original_pos, dtype='f4'),
                        'velocity': np.array((
                            self.random_cache.uniform(-0.05, 0.05),
                            self.random_cache.uniform(-0.05, 0.05),
                            self.random_cache.uniform(0.8, 1.2)
                        )),
                        'color': self.colors[idx].copy(),
                        'birth_time': self.last_time
                    })
                self.stars[idx] = self.get_star_position()


    def update(self, current_time: float, midi_messages: list):
        elapsed_time = current_time - self.last_time
        self.last_time = current_time

        # Update rotation
        self.rotation_angle += self.rotation_speed * elapsed_time
        self.program['z_rotation'].write(calculate_rotation_matrix_jit(self.rotation_angle, 0, 0, 1).tobytes())

        # Update regular star positions and reset those that get too close
        self.stars[:, 2] += self.base_speed * elapsed_time
        mask = self.stars[:, 2] > -5.0
        if np.any(mask):
            new_positions = np.array([self.get_star_position() for _ in range(np.sum(mask))])
            self.stars[mask] = new_positions
            offset = mask.nonzero()[0][0] * self.stars.itemsize
            length = np.sum(mask) * self.stars.itemsize

            self.ibo.write(new_positions.tobytes(), offset=offset)
        
        # Write all star positions to buffer
        self.ibo.write(self.stars.tobytes())

        # Handle MIDI controls



        speed_update_cc = get_latest_cc(midi_messages, self.starfield_speed_cc,self.starfield_speed_channel)
        if speed_update_cc > -1:
            self.base_speed = apply_curve(speed_update_cc, 0.5) * 5 + 1
            print (f"base speed = {self.base_speed}")

        rotate_cc = get_latest_cc(midi_messages, self.starfield_rotation_cc,self.starfield_rotation_channel)
        if rotate_cc > -1:
            self.rotation_speed = 10*sigmoid(rotate_cc, 60, 0.05, 0.5) 


        # Handle explosions
        if self.base_speed < 60:
            latest_note = get_latest_note(midi_messages, channel=0)
            if latest_note['note'] != -1 and latest_note['note'] < 48:
                self.explode_star_group(latest_note['note'] % self.num_groups)

        # Update existing explosions
        for _ in range(len(self.exploding_stars)):
            star = self.exploding_stars.popleft()
            star['position'] += star['velocity'] * self.base_speed * elapsed_time
            age = current_time - star['birth_time']
            if age <= self.explosion_duration:
                star['color'][3] = (1 - age / self.explosion_duration) ** 4
                self.exploding_stars.append(star)

    def render_effect(self):
        # Render regular stars
        self.star_vao.render(moderngl.TRIANGLE_FAN, vertices=len(self.vertices), instances=self.num_stars)
        
        # Then render exploding stars if there are any
        if self.exploding_stars:
            exploding_positions = np.array([s['position'] for s in self.exploding_stars], dtype='f4')
            exploding_colors = np.array([s['color'] for s in self.exploding_stars], dtype='f4')
            
            self.context.enable(moderngl.BLEND)
            self.context.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            
            exploding_ibo = self.context.buffer(exploding_positions.tobytes())
            exploding_cbo = self.context.buffer(exploding_colors.tobytes())
            
            exploding_vao = self.context.vertex_array(
                self.program, [
                    (self.vbo, '3f', 'in_vert'),
                    (exploding_cbo, '4f/i', 'in_color'),
                    (exploding_ibo, '3f/i', 'in_offset')
                ]
            )
            
            exploding_vao.render(moderngl.TRIANGLE_FAN, vertices=len(self.vertices), instances=len(self.exploding_stars))
            
            exploding_ibo.release()
            exploding_cbo.release()
            exploding_vao.release()
            
            self.context.disable(moderngl.BLEND)

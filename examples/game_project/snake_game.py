"""校园贪吃蛇 — OJ Sentinel 作品评测示例（非真实学生作业）。

运行：python snake_game.py
依赖：Python 3.10+，tkinter（标准库）。
"""
from __future__ import annotations

import random
import tkinter as tk

GRID = 20
CELL = 24
START_DELAY = 140
FOOD_SCORE = 10


class SnakeGame:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("校园贪吃蛇 · Campus Snake")
        self.canvas = tk.Canvas(root, width=GRID * CELL, height=GRID * CELL, bg="#101820")
        self.canvas.pack()
        self.status = tk.Label(root, text="", font=("Menlo", 13), anchor="w")
        self.status.pack(fill="x")
        self.root.bind("<Key>", self.on_key)
        self.reset()

    def reset(self) -> None:
        self.snake = [(10, 10), (10, 11), (10, 12)]
        self.direction = (0, -1)
        self.pending = (0, -1)
        self.score = 0
        self.delay = START_DELAY
        self.running = True
        self.food = self.spawn_food()
        self.loop()

    def spawn_food(self) -> tuple[int, int]:
        empty = [(x, y) for x in range(GRID) for y in range(GRID) if (x, y) not in self.snake]
        return random.choice(empty)

    def on_key(self, event: tk.Event) -> None:
        key = event.keysym.lower()
        if key == "r":
            self.reset()
            return
        if key == "escape":
            self.root.destroy()
            return
        if not self.running:
            return
        mapping = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
        if key in mapping:
            dx, dy = mapping[key]
            if (dx, dy) != (-self.direction[0], -self.direction[1]):
                self.pending = (dx, dy)

    def loop(self) -> None:
        if not self.running:
            return
        self.direction = self.pending
        head = (self.snake[0][0] + self.direction[0], self.snake[0][1] + self.direction[1])
        if not (0 <= head[0] < GRID and 0 <= head[1] < GRID) or head in self.snake:
            self.running = False
            self.draw()
            self.status.config(text=f"游戏结束！最终分数：{self.score}    按 R 重新开始，ESC 退出")
            return
        self.snake.insert(0, head)
        if head == self.food:
            self.score += FOOD_SCORE
            self.food = self.spawn_food()
            if self.delay > 55:
                self.delay -= 5
        else:
            self.snake.pop()
        self.draw()
        self.root.after(self.delay, self.loop)

    def draw(self) -> None:
        self.canvas.delete("all")
        for row in range(GRID):
            for col in range(GRID):
                if (row + col) % 2 == 0:
                    self.canvas.create_rectangle(
                        col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL,
                        fill="#16222e", outline="",
                    )
        fx, fy = self.food
        self.canvas.create_oval(
            fx * CELL + 4, fy * CELL + 4, (fx + 1) * CELL - 4, (fy + 1) * CELL - 4,
            fill="#ffb020", outline="",
        )
        for index, (x, y) in enumerate(self.snake):
            color = "#3ecf8e" if index else "#5b8cff"
            self.canvas.create_rectangle(
                x * CELL + 1, y * CELL + 1, (x + 1) * CELL - 1, (y + 1) * CELL - 1,
                fill=color, outline="",
            )
        self.status.config(text=f"分数：{self.score}    长度：{len(self.snake)}    速度：{START_DELAY - self.delay + 1}")


def main() -> None:
    root = tk.Tk()
    root.resizable(False, False)
    SnakeGame(root)
    root.mainloop()


if __name__ == "__main__":
    main()

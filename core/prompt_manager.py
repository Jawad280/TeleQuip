import random

class PromptManager:
    def __init__(self, path="data/prompts.txt"):
        # open the file and read all non-empty lines
        with open(path, "r", encoding="utf-8") as f:
            self.prompts = [line.strip() for line in f if line.strip()]

    def get_random_prompts(self, count):
        # safely sample prompts
        if count > len(self.prompts):
            count = len(self.prompts)
        return random.sample(self.prompts, count)
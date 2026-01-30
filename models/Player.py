class Player:
    def __init__(self, user_id, username):
        self.user_id = user_id
        self.username = username
        self.answers = {}  # {round_num: answer_text}
        self.score = 0

    def submit_answer(self, round_num, answer):
        self.answers[round_num] = answer

    def add_score(self, points):
        self.score += points

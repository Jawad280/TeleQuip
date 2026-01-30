class Game:
    def __init__(self, group_id):
        self.group_id = group_id
        self.players = {}
        self.round = 0
        
        self.prompts = []
        self.locked = False
        self.pending_answers = {} # { user_id: None } initially none for each player
        
        self.versus_pairs = []        # [(prompt, player1_id, player2_id)]
        self.scores = {uid: 0 for uid in self.players}  # current round cumulative scores
        self.votes = {}               # {poll_message_id: {voter_id: choice_index}}

        self.completed_polls = set()

    def add_player(self, player):
        self.players[player.user_id] = player

    def is_full(self, max_players):
        return len(self.players) >= max_players
    
    def reset(self):
        self.round = 0
        self.prompts = []
        self.versus_pairs = []
        self.scores = {}
        self.locked = False
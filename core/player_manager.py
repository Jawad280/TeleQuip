from models.Player import Player

class PlayerManager:
    @staticmethod
    def add_player(game, user_id, username):
        if user_id not in game.players:
            game.add_player(Player(user_id, username))
            return True
        return False

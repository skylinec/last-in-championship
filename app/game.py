import random
import json
from datetime import datetime
from sqlalchemy import text
from typing import Dict, List, Optional, Union

def is_valid_move(game_state: Dict, move: int) -> bool:
    """Check if move is valid for the current game state"""
    if not game_state or not isinstance(game_state, dict):
        return False
        
    board = game_state.get('board', [])
    game_type = game_state.get('game_type', 'tictactoe')
    
    try:
        position = int(move)
        if game_type == 'tictactoe':
            return 0 <= position < 9 and not board[position]
        elif game_type == 'connect4':
            col = position
            if not (0 <= col < 7):
                return False
            for row in range(5, -1, -1):
                if not board[row * 7 + col]:
                    return True
        return False
    except (ValueError, TypeError, IndexError):
        return False

def apply_move(game_state: Dict, move: int, player: str) -> Dict:
    """Apply move to game state and return updated state"""
    if not is_valid_move(game_state, move):
        raise ValueError("Invalid move")

    board = game_state['board'].copy()
    game_type = game_state.get('game_type', 'tictactoe')
    
    if game_type == 'tictactoe':
        board[move] = player
    else:  # Connect4
        for row in range(5, -1, -1):
            pos = row * 7 + move
            if not board[pos]:
                board[pos] = player
                break

    return {
        **game_state,
        'board': board,
        'moves': [*game_state.get('moves', []), {
            'player': player,
            'position': move,
            'timestamp': datetime.now().isoformat()
        }],
        'current_player': (
            game_state['player2'] 
            if player == game_state['player1']
            else game_state['player1']
        )
    }

def check_winner(game_state: Dict, game_type: str) -> Optional[str]:
    """Check if there's a winner in the current game state"""
    if not game_state or 'board' not in game_state:
        return None
        
    board = game_state['board']
    
    # Check for each player
    for player in [game_state.get('player1'), game_state.get('player2')]:
        if not player:
            continue
            
        if game_type == 'tictactoe':
            if check_tictactoe_winner(board, player):
                return player
        elif game_type == 'connect4':
            if check_connect4_winner(board, player):
                return player
            
    # Check for draw
    if all(cell is not None for cell in board):
        return 'draw'
        
    return None

def check_tictactoe_winner(board: List, player: str) -> bool:
    """Check if player has won in tic-tac-toe"""
    winning_combinations = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],  # Rows
        [0, 3, 6], [1, 4, 7], [2, 5, 8],  # Columns
        [0, 4, 8], [2, 4, 6]              # Diagonals
    ]
    return any(
        all(board[pos] == player for pos in combo)
        for combo in winning_combinations
    )

def check_connect4_winner(board: List, player: str) -> bool:
    """Check if player has won in Connect 4"""
    # Check horizontal
    for row in range(6):
        for col in range(4):
            if all(board[row * 7 + col + i] == player for i in range(4)):
                return True
                
    # Check vertical
    for row in range(3):
        for col in range(7):
            if all(board[(row + i) * 7 + col] == player for i in range(4)):
                return True
                
    # Check diagonal (positive slope)
    for row in range(3):
        for col in range(4):
            if all(board[(row + i) * 7 + col + i] == player for i in range(4)):
                return True
                
    # Check diagonal (negative slope)
    for row in range(3):
        for col in range(3, 7):
            if all(board[(row + i) * 7 + col - i] == player for i in range(4)):
                return True
                
    return False

def create_test_games(db, tie_id, users):
    """Helper function to create test games for a tie breaker"""
    if len(users) < 2:
        return

    game_types = ['tictactoe', 'connect4']
    
    # Create one of each game type
    for game_type in game_types:
        # Randomly assign players
        player1, player2 = random.sample(users, 2)
        
        # Initialize game state
        board_size = 9 if game_type == 'tictactoe' else 42
        game_state = {
            'board': [None] * board_size,
            'moves': [],
            'current_player': player1,
            'player1': player1,
            'player2': player2,
            'game_type': game_type
        }

        # Create game
        db.execute(text("""
            INSERT INTO tie_breaker_games (
                tie_breaker_id,
                game_type,
                player1,
                player2,
                status,
                game_state
            ) VALUES (
                :tie_id,
                :game_type,
                :player1,
                :player2,
                'active',
                :game_state
            )
        """), {
            "tie_id": tie_id,
            "game_type": game_type,
            "player1": player1,
            "player2": player2,
            "game_state": json.dumps(game_state)
        })

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy import text
import logging
import random
import json

from .database import SessionLocal
from .models import TieBreaker, TieBreakerParticipant, TieBreakerGame

def create_test_tie_breaker(db, period: str, period_end: datetime, points: float, mode: str, users: List[str]) -> Optional[int]:
    """Create a test tie breaker for development/testing"""
    try:
        logging.info(f"Creating test tie breaker: period={period}, end={period_end}, mode={mode}, users={users}")
        
        # Ensure period_end is a datetime
        if isinstance(period_end, str):
            period_end = datetime.strptime(period_end, '%Y-%m-%d')
        
        # Calculate period start
        days_back = 7 if period == 'weekly' else 30
        period_start = period_end - timedelta(days=days_back)
        
        logging.info(f"Period start: {period_start}, Period end: {period_end}")
        
        # Insert tie breaker
        result = db.execute(text("""
            INSERT INTO tie_breakers (
                period,
                period_start,
                period_end,
                points,
                mode,
                status
            ) VALUES (
                :period,
                :period_start,
                :period_end,
                :points,
                :mode,
                'pending'
            ) RETURNING id
        """), {
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            "points": points,
            "mode": mode
        })

        tie_id = result.fetchone()[0]
        logging.info(f"Created tie breaker with ID: {tie_id}")

        # Add participants
        for user in users:
            game_choice = random.choice(['tictactoe', 'connect4'])
            db.execute(text("""
                INSERT INTO tie_breaker_participants (
                    tie_breaker_id, 
                    username,
                    game_choice,
                    ready
                ) VALUES (
                    :tie_id,
                    :username,
                    :game_choice,
                    true
                )
            """), {
                "tie_id": tie_id,
                "username": user,
                "game_choice": game_choice
            })
            logging.info(f"Added participant {user} with game choice {game_choice}")

        return tie_id

    except Exception as e:
        logging.error(f"Error creating test tie breaker: {str(e)}", exc_info=True)
        raise

def create_next_game(db, tie_id: int) -> Optional[int]:
    """Create next available games between tied participants"""
    try:
        # Get tie breaker details
        tie_breaker = db.execute(text("""
            SELECT 
                t.id,
                t.status,
                t.period_end,
                t.points,
                t.mode,
                array_agg(p.username) as participants
            FROM tie_breakers t
            JOIN tie_breaker_participants p ON t.id = p.tie_breaker_id
            WHERE t.id = :tie_id
            GROUP BY t.id
        """), {"tie_id": tie_id}).fetchone()

        if not tie_breaker or tie_breaker.status != 'in_progress':
            return None

        # Get remaining possible pairs
        pairs = db.execute(text("""
            SELECT 
                p1.username as player1,
                p1.game_choice as player1_choice, 
                p2.username as player2,
                p2.game_choice as player2_choice
            FROM tie_breaker_participants p1
            CROSS JOIN tie_breaker_participants p2
            WHERE p1.tie_breaker_id = :tie_id
            AND p2.tie_breaker_id = :tie_id
            AND p1.username < p2.username
            AND p1.ready = true AND p2.ready = true
            AND NOT EXISTS (
                SELECT 1 FROM tie_breaker_games g
                WHERE g.tie_breaker_id = :tie_id
                AND ((g.player1 = p1.username AND g.player2 = p2.username)
                    OR (g.player1 = p2.username AND g.player2 = p1.username))
            )
        """), {"tie_id": tie_id}).fetchall()

        created_game_ids = []
        for pair in pairs:
            game_choices = [pair.player1_choice, pair.player2_choice]
            if not all(choice in ['tictactoe', 'connect4'] for choice in game_choices if choice):
                continue

            if pair.player1_choice == pair.player2_choice:
                game_id = create_game(db, tie_id, pair.player1_choice, pair.player1, pair.player2)
                if game_id:
                    created_game_ids.append(game_id)
            else:
                # Create two games, one with each player's choice
                for player, choice in [(pair.player1, pair.player1_choice), 
                                     (pair.player2, pair.player2_choice)]:
                    game_id = create_game(db, tie_id, choice, player, 
                                        pair.player2 if player == pair.player1 else pair.player1)
                    if game_id:
                        created_game_ids.append(game_id)

        return created_game_ids[0] if created_game_ids else None

    except Exception as e:
        logging.error(f"Error creating next game: {str(e)}")
        raise

def create_game(db, tie_id: int, game_type: str, player1: str, player2: str) -> Optional[int]:
    """Create a new game with initial state"""
    try:
        board_size = 9 if game_type == 'tictactoe' else 42
        initial_state = {
            'board': [None] * board_size,
            'moves': [],
            'current_player': player1,
            'player1': player1,
            'player2': player2,
            'game_type': game_type
        }

        result = db.execute(text("""
            INSERT INTO tie_breaker_games (
                tie_breaker_id,
                game_type,
                player1,
                player2,
                status,
                game_state,
                final_tiebreaker
            ) VALUES (
                :tie_id,
                :game_type,
                :player1,
                :player2,
                'pending',
                :game_state,
                false
            ) RETURNING id
        """), {
            "tie_id": tie_id,
            "game_type": game_type,
            "player1": player1,
            "player2": player2,
            "game_state": json.dumps(initial_state)
        })

        game_id = result.fetchone()[0]

        # Immediately activate game since both players are ready
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET status = 'active'
            WHERE id = :game_id
        """), {"game_id": game_id})

        return game_id

    except Exception as e:
        logging.error(f"Error creating game: {str(e)}")
        raise

def create_next_game_after_draw(db, tie_id: int, game_type: str, player1: str, player2: str) -> Optional[int]:
    """Create a new game after a draw with reversed player order"""
    try:
        # Initialize new game state with reversed player order
        board_size = 9 if game_type == 'tictactoe' else 42
        initial_state = {
            'board': [None] * board_size,
            'moves': [],
            'current_player': player2,  # Reversed order
            'player1': player2,         # Reversed order
            'player2': player1,         # Reversed order
            'game_type': game_type
        }

        result = db.execute(text("""
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
            ) RETURNING id
        """), {
            "tie_id": tie_id,
            "game_type": game_type,
            "player1": player2,  # Reversed order
            "player2": player1,  # Reversed order
            "game_state": json.dumps(initial_state)
        })

        return result.fetchone()[0]

    except Exception as e:
        logging.error(f"Error creating next game after draw: {str(e)}")
        raise

def check_tie_breaker_completion(db, tie_id: int) -> bool:
    """Check if tie breaker is complete and determine winner"""
    try:
        # Get tie breaker period end date first
        tie_breaker = db.execute(text("""
            SELECT period_end, points, mode, points_applied
            FROM tie_breakers 
            WHERE id = :tie_id
        """), {"tie_id": tie_id}).fetchone()

        if not tie_breaker or tie_breaker.points_applied:
            return False

        # Check if all non-final games are complete
        completed = db.execute(text("""
            SELECT 
                COUNT(*) = COUNT(winner) as all_complete,
                array_agg(winner) FILTER (WHERE winner IS NOT NULL) as winners
            FROM tie_breaker_games
            WHERE tie_breaker_id = :tie_id
            AND NOT final_tiebreaker
        """), {"tie_id": tie_id}).fetchone()

        if completed.all_complete:
            # Determine overall winner based on most wins
            winner = determine_winner(db, tie_id)
            if winner:
                # Update tie breaker status
                db.execute(text("""
                    UPDATE tie_breakers
                    SET status = 'completed',
                        resolved_at = NOW(),
                        points_applied = true
                    WHERE id = :tie_id
                """), {"tie_id": tie_id})

                # Update participant as winner
                db.execute(text("""
                    UPDATE tie_breaker_participants
                    SET winner = (username = :winner)
                    WHERE tie_breaker_id = :tie_id
                """), {
                    "tie_id": tie_id,
                    "winner": winner
                })

                return True

        return False

    except Exception as e:
        logging.error(f"Error checking tie breaker completion: {str(e)}")
        return False

def determine_winner(db, tie_id: int) -> Optional[str]:
    """Determine the winner across all games"""
    try:
        # Get wins per player
        wins = db.execute(text("""
            SELECT 
                winner as player,
                COUNT(*) as wins
            FROM tie_breaker_games
            WHERE tie_breaker_id = :tie_id
            AND winner IS NOT NULL
            AND NOT final_tiebreaker
            GROUP BY winner
            ORDER BY wins DESC
        """), {"tie_id": tie_id}).fetchall()

        if not wins:
            return None

        # If there's a clear winner, return them
        if len(wins) == 1 or wins[0].wins > wins[1].wins:
            return wins[0].player

        # If tie, create final tie-breaker game
        top_players = [w.player for w in wins[:2]]
        game_type = random.choice(['tictactoe', 'connect4'])
        
        create_game(db, tie_id, game_type, top_players[0], top_players[1])
        return None

    except Exception as e:
        logging.error(f"Error determining winner: {str(e)}")
        return None

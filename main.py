@app.route("/tie-breakers")
@login_required
def tie_breakers():
    db = SessionLocal()
    try:
        # Updated query to properly show active and available games
        tie_breakers = db.execute(text("""
            WITH tie_breakers_cte AS (
                SELECT 
                    t.id,
                    t.period,
                    t.period_start,
                    t.period_end,
                    t.points,
                    t.status,
                    t.created_at,
                    t.resolved_at,
                    jsonb_agg(jsonb_build_object(
                        'username', tp.username,
                        'game_choice', tp.game_choice,
                        'ready', tp.ready,
                        'winner', tp.winner
                    )) as participants,
                    (
                        SELECT jsonb_agg(jsonb_build_object(
                            'id', g.id,
                            'player1', g.player1,
                            'player2', g.player2,
                            'status', g.status,
                            'game_type', g.game_type
                        ))
                        FROM tie_breaker_games g
                        WHERE g.tie_breaker_id = t.id
                        AND (g.status != 'completed')  -- Show all non-completed games
                    ) as available_games
                FROM tie_breakers t
                JOIN tie_breaker_participants tp ON t.id = tp.tie_breaker_id
                WHERE t.status IN ('pending', 'in_progress')  -- Include both pending and in_progress
                GROUP BY t.id
            )
            SELECT * FROM tie_breakers_cte
            ORDER BY created_at DESC
        """)).fetchall()
        
        return render_template(
            "tie_breakers.html",
            tie_breakers=tie_breakers,
            current_user=session['user']
        )
    finally:
        db.close()

@app.route("/games/<int:game_id>/join", methods=["POST"])
@login_required
def join_game(game_id):
    db = SessionLocal()
    try:
        # Updated query to check game status properly
        game = db.execute(text("""
            SELECT g.*, t.id as tie_id, t.status as tie_status
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id
            WHERE g.id = :game_id
        """), {"game_id": game_id}).fetchone()

        # Check if game exists and tie breaker is active
        if not game or game.tie_status not in ['pending', 'in_progress']:
            return jsonify({"error": "Game not available"}), 400

        # Check if game is already full
        if game.status == 'active' or game.player2 is not None:
            return jsonify({"error": "Game already has two players"}), 400

        # Check if user is eligible to join
        is_eligible = db.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM tie_breaker_participants
                WHERE tie_breaker_id = :tie_id
                AND username = :username
                AND username != :player1
            )
        """), {
            "tie_id": game.tie_id,
            "username": session['user'],
            "player1": game.player1
        }).scalar()

        if not is_eligible:
            return jsonify({"error": "Not eligible to join this game"}), 403

        # Add player2 and activate game
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET player2 = :player2,
                status = 'active',
                game_state = jsonb_set(
                    game_state::jsonb,
                    '{moves}',
                    jsonb_build_array(
                        jsonb_build_object(
                            'player', :player1,
                            'timestamp', :timestamp
                        ),
                        jsonb_build_object(
                            'player', :player2,
                            'timestamp', :timestamp
                        )
                    )
                )
            WHERE id = :game_id
            AND status = 'available'
        """), {
            "player2": session['user'],
            "game_id": game_id,
            "player1": game.player1,
            "timestamp": datetime.now().isoformat()
        })

        db.commit()
        return redirect(url_for('play_game', game_id=game_id))
    finally:
        db.close()

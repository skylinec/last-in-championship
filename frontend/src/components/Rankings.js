import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { API_URL } from '../config';

function Rankings() {
  const [rankings, setRankings] = useState([]);
  const [loading, setLoading] = useState(true);
  const { period } = useParams();

  useEffect(() => {
    const fetchRankings = async () => {
      try {
        const response = await fetch(`${API_URL}/api/rankings/${period}`);
        const data = await response.json();
        setRankings(data.rankings);
        setLoading(false);
      } catch (error) {
        console.error('Error fetching rankings:', error);
        setLoading(false);
      }
    };

    fetchRankings();
  }, [period]);

  if (loading) return <div>Loading...</div>;

  return (
    <div className="rankings-container">
      <h1>{period.charAt(0).toUpperCase() + period.slice(1)} Rankings</h1>
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Name</th>
            <th>Score</th>
            <th>Average Arrival</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {rankings.map((rank, index) => (
            <tr key={rank.name}>
              <td>{index + 1}</td>
              <td>{rank.name}</td>
              <td>{rank.score}</td>
              <td>{rank.average_arrival_time}</td>
              <td>
                Office: {rank.stats.in_office} days<br />
                Remote: {rank.stats.remote} days
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default Rankings;

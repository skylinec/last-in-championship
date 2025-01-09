import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Navigation from './components/Navigation';
import Rankings from './components/Rankings';
import History from './components/History';
import Settings from './components/Settings';
import LogAttendance from './components/LogAttendance';

function App() {
  return (
    <BrowserRouter>
      <div className="App">
        <Navigation />
        <Routes>
          <Route path="/" element={<LogAttendance />} />
          <Route path="/rankings/:period" element={<Rankings />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;

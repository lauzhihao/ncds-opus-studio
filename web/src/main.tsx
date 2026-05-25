import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

import { TemplatesPage } from './routes/TemplatesPage';
import { JobCanvasPage } from './routes/JobCanvasPage';
import { applyThemeFromStorage } from './hooks/useTheme';
import './styles/global.css';
import 'reactflow/dist/style.css';

// 同步阶段就把主题打到 <html>，避免首屏闪烁
applyThemeFromStorage();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/studio">
      <Routes>
        <Route path="/" element={<TemplatesPage />} />
        <Route path="/jobs/:jobId" element={<JobCanvasPage />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);

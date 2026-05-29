import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

import { TemplatesPage } from './routes/TemplatesPage';
import { JobCanvasPage } from './routes/JobCanvasPage';
import { ToastProvider } from './components/Toast';
import { applyThemeFromStorage } from './hooks/useTheme';
import './styles/themes.css';
import './styles/global.scss';
import 'reactflow/dist/style.css';

// 同步阶段就把主题打到 <html>，避免首屏闪烁
applyThemeFromStorage();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ToastProvider>
      <BrowserRouter
        basename="/studio"
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<TemplatesPage />} />
          <Route path="/jobs/:jobId" element={<JobCanvasPage />} />
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  </React.StrictMode>,
);

import { Suspense, lazy } from 'react';

import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Skeleton } from '@/components/ui/skeleton';

import { useFetch } from 'hooks/useFetch';

import { type IPlotlyElement } from 'client-types/';

const Plot = lazy(() => import('react-plotly.js'));

interface Props {
  element: IPlotlyElement;
}

const _PlotlyElement = ({ element }: Props) => {
  const { data, error, isLoading } = useFetch(element.url || null);

  const fallbackFromProps = (() => {
    const raw: any = (element as any)?.props;
    if (!raw) {
      return null;
    }
    let propsObj: any = raw;
    if (typeof raw === 'string') {
      try {
        propsObj = JSON.parse(raw);
      } catch {
        return null;
      }
    }
    if (!propsObj || typeof propsObj !== 'object') {
      return null;
    }
    const figure = propsObj.figure_json;
    if (figure && typeof figure === 'object') {
      return figure;
    }
    return null;
  })();

  if (isLoading) {
    return <div>Loading...</div>;
  } else if (error && !fallbackFromProps) {
    return <div>An error occurred</div>;
  }

  const normalizeFigureState = (input: any): any => {
    if (!input) return null;
    if (typeof input === 'string') {
      const text = input.trim();
      if (!text) return null;
      if (text.startsWith('<!doctype') || text.startsWith('<html')) {
        return null;
      }
      try {
        return normalizeFigureState(JSON.parse(text));
      } catch {
        return null;
      }
    }
    if (typeof input !== 'object') return null;
    if ((input as any).figure_json && typeof (input as any).figure_json === 'object') {
      return (input as any).figure_json;
    }
    return input;
  };

  const state = normalizeFigureState(data) || normalizeFigureState(fallbackFromProps);
  if (!state || !Array.isArray((state as any).data)) {
    return <div>An error occurred</div>;
  }

  return (
    <Suspense fallback={<Skeleton className="h-full rounded-md" />}>
      <Plot
        className={`${element.display}-plotly`}
        data={state.data}
        layout={state.layout}
        frames={state.frames}
        config={state.config}
        style={{
          width: '100%',
          height: '100%',
          borderRadius: '1rem',
          overflow: 'hidden'
        }}
        useResizeHandler={true}
      />
    </Suspense>
  );
};

const PlotlyElement = (props: Props) => {
  return (
    <ErrorBoundary prefix="Failed to load chart.">
      <_PlotlyElement {...props} />
    </ErrorBoundary>
  );
};

export { PlotlyElement };

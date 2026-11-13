import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {CleanupProgressIndicator} from '../CleanupProgressIndicator';
afterEach(cleanup);
it('shows reported download progress without labeling it as an estimate',()=>{
 render(<CleanupProgressIndicator progress={{phase:'loading',percent:42}}/>);
 expect(screen.getByText('Loading cleanup model · 42%')).toBeVisible();
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','42');
});
it('keeps an inference estimate below 100 and shows 100 only for ready output',()=>{
 const progress={phase:'cleaning' as const,startedAt:Date.now()-120000,expectedMs:15000};
 const view=render(<CleanupProgressIndicator progress={progress}/>);
 expect(screen.getByText('Cleaning background · 95% (estimated)')).toBeVisible();
 view.rerender(<CleanupProgressIndicator progress={progress} complete/>);
 expect(screen.getByText('Ready · 100%')).toBeVisible();
 expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});
it('retains elapsed progress when mounted on the waiting screen',()=>{
 render(<CleanupProgressIndicator progress={{phase:'cleaning',startedAt:Date.now()-5000,expectedMs:15000}}/>);
 expect(screen.getByText(/Cleaning background · 30% \(estimated\)/)).toBeVisible();
});

it('makes estimated cleaning progress prominent without claiming completion',()=>{
 render(<CleanupProgressIndicator variant="prominent" label="Removing background"
  progress={{phase:'cleaning',startedAt:Date.now()-120000,expectedMs:15000}}/>);
 expect(screen.getByText('Removing background')).toBeVisible();
 expect(screen.getByText('estimated')).toBeVisible();
 expect(screen.getByRole('progressbar',{name:'Removing background'})).toHaveAttribute('aria-valuenow','95');
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext','95% estimated');
 expect(screen.queryByText('Photo ready')).not.toBeInTheDocument();
});
it('uses indeterminate prominent progress until a download reports a percentage',()=>{
 const view=render(<CleanupProgressIndicator variant="prominent" progress={{phase:'loading'}}/>);
 expect(screen.getByText('Loading photo tools')).toBeVisible();
 expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
 view.rerender(<CleanupProgressIndicator variant="prominent" progress={{phase:'loading',percent:55}}/>);
 expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','55');
 expect(screen.queryByText('estimated')).not.toBeInTheDocument();
});

import type { ReportJob } from '../types';
import { ReportCard } from './ReportCard';
import { Empty } from './Empty';
import './ReportTimeline.css';

type Props = {
  jobs: ReportJob[];
  onOpen: (job: ReportJob) => void;
};

export function ReportTimeline({ jobs, onOpen }: Props) {
  if (jobs.length === 0) {
    return <Empty message="No reports yet — promote a Risk view or generate one to populate." symbol="◌" />;
  }
  return (
    <div className="wl-timeline">
      {jobs.map((job) => (
        <ReportCard key={job.id} job={job} onOpen={onOpen} />
      ))}
    </div>
  );
}

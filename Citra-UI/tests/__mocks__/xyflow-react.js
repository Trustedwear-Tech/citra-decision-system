// Stub for @xyflow/react so tests don't need the real React Flow
const React = require('react');

const ReactFlow = (props) =>
  React.createElement('div', { 'data-testid': 'react-flow' }, props.children);

const Background = () => React.createElement('div', { 'data-testid': 'rf-background' });
const Controls = () => React.createElement('div', { 'data-testid': 'rf-controls' });
const MiniMap = () => React.createElement('div', { 'data-testid': 'rf-minimap' });

const Handle = (props) =>
  React.createElement('div', { 'data-testid': `handle-${props.type}-${props.id || '0'}` });

const Position = { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' };
const MarkerType = { ArrowClosed: 'arrowclosed' };

function useNodesState(initial = []) {
  const [nodes, setNodes] = React.useState(initial);
  return [nodes, setNodes, jest.fn()];
}

function useEdgesState(initial = []) {
  const [edges, setEdges] = React.useState(initial);
  return [edges, setEdges, jest.fn()];
}

function useReactFlow() {
  return {
    screenToFlowPosition: jest.fn((pos) => pos),
    fitView: jest.fn(),
    getNodes: jest.fn(() => []),
    getEdges: jest.fn(() => []),
  };
}

function addEdge(params, edges) {
  return [...edges, { id: `e-${params.source}-${params.target}`, ...params }];
}

const ReactFlowProvider = ({ children }) =>
  React.createElement('div', { 'data-testid': 'rf-provider' }, children);

module.exports = {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  useReactFlow,
  addEdge,
  ReactFlowProvider,
};
